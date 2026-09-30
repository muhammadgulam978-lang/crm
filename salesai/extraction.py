"""
AI fact extraction with hard guardrails against invention.

The model returns facts; this module then CHECKS them before anything is stored:
  1. the fact must cite a real source (source_ref) that was sent to the model;
  2. "stated" facts must include a quote that appears word-for-word in that source;
  3. numbers in the fact (budgets, dates, sizes) must also appear in the quote;
  4. hedged quotes ("maybe", "seems", "probably" ...) can never be verified;
  5. a timeline bucket / decision role is only kept when the fact itself is verified.
Anything that fails a check is kept as an ASSUMPTION (clearly labelled) or dropped - never verified.
"""
import re

from django.utils import timezone

from .llm import AIUnavailable, chat_json
from .models import (ASSUMED, FACT_KEY_LABELS, REJECTED, ROLE_CHOICES, TIMELINE_BUCKETS, VERIFIED,
                     CustomerFact, normalize_text)
from .sources import collect_sources

VALID_KEYS = set(FACT_KEY_LABELS)
VALID_BUCKETS = {b for b, _ in TIMELINE_BUCKETS}
VALID_ROLES = {r for r, _ in ROLE_CHOICES}
HEDGES = re.compile(
    r"\b(maybe|perhaps|probably|possibly|might|seems?|seemed|appears?|i think|i guess|assum\w*|"
    r"not sure|unclear|likely|could be|shayad|lagta hai|ho sakta)\b", re.I)
_NUM = re.compile(r'\d[\d,\.]*')

EXTRACTION_SYSTEM = """You extract facts about a sales lead from CRM records for a software company's sales team.
STRICT RULES
1. Use ONLY the records in "sources". Never invent, guess, generalise or fill gaps. If something is not written there, leave it out.
2. Every fact needs "source_ref" (a ref from sources) and "evidence": an EXACT quote copied character for character from that source (max 200 characters).
3. "basis" is "stated" ONLY if the quote directly says it. Use "inferred" for anything you conclude yourself. Hedged wording (maybe, seems, probably) is always "inferred".
4. Never output prices, budgets, dates, sizes or other numbers unless they appear inside the quote.
5. A note written by staff is not a customer statement unless it explicitly reports what the customer said.
6. Do not repeat the same fact twice. Prefer few, precise facts over many.
7. "sis_offers" is only a list of what the company sells. Do NOT decide which offer fits, do not describe any offer, and do not add facts because an offer would suit them.
Return ONLY a JSON object: {"facts":[{"key","value","basis","source_ref","evidence","bucket","role"}]}
"key" is one of: industry (the customer's sector / type of organisation, e.g. school, clinic, retailer), need (what the customer wants to achieve), requirement (a specific thing the solution must do/have), pain_point (the main business problem the customer describes), scale (size of the operation, e.g. number of campuses, branches, staff or users, only if stated), decision_role, decision_process, budget, timeline, objection (concern/hesitation raised), open_question (a question the CUSTOMER asked), competitor, current_solution.
"bucket" only for key=timeline: immediate (<=30 days), near (<=90 days), medium (<=6 months), long (later), researching (no date / still researching).
"role" only for key=decision_role: decision_maker, influencer, user, gatekeeper.
"value" is a short neutral phrase (max 25 words) using the customer's own words where possible."""


def _norm_ws(s: str) -> str:
    return re.sub(r'\s+', ' ', (s or '')).strip().lower()


def quote_in_source(quote: str, source_text: str) -> bool:
    q, s = _norm_ws(quote).strip(' "\'“”‘’.'), _norm_ws(source_text)
    return len(q) >= 4 and q in s


def numbers_supported(value: str, evidence: str) -> bool:
    ev = re.sub(r'[,\s]', '', evidence or '')
    for tok in _NUM.findall(value or ''):
        tok = tok.rstrip('.,').replace(',', '')
        if tok and tok not in ev:
            return False
    return True


def validate_facts(raw_facts, sources_by_ref):
    """-> list of dicts ready for CustomerFact(**d). Never raises on bad model output."""
    out, seen = [], set()
    for f in (raw_facts or [])[:60]:
        if not isinstance(f, dict):
            continue
        key = str(f.get('key', '')).strip()
        value = re.sub(r'\s+', ' ', str(f.get('value', '')).strip())[:400]
        if key not in VALID_KEYS or key == 'other' or len(value) < 3:
            continue
        ref, quote = str(f.get('source_ref', '')).strip(), str(f.get('evidence', '')).strip()[:400]
        src = sources_by_ref.get(ref)
        basis = str(f.get('basis', 'inferred')).strip().lower()
        notes, ok = [], False
        if not src:
            notes.append('The AI did not cite a valid CRM record.')
        elif not quote_in_source(quote, src.text):
            notes.append('The quoted evidence was not found word-for-word in the record.')
        elif not numbers_supported(value, quote):
            notes.append('A number in this fact is not present in the quote.')
        elif HEDGES.search(quote):
            notes.append('The source itself is tentative (hedged wording).')
        elif basis != 'stated':
            notes.append('The AI inferred this; it is not stated directly.')
        else:
            ok = True
        extra = {}
        if ok and key == 'timeline' and f.get('bucket') in VALID_BUCKETS:
            extra['bucket'] = f['bucket']
        if ok and key == 'decision_role' and f.get('role') in VALID_ROLES:
            extra['role'] = f['role']
        dedup = (key, normalize_text(value))
        if dedup in seen:
            continue
        seen.add(dedup)
        out.append(dict(
            key=key, value=value, status=VERIFIED if ok else ASSUMED, extra=extra, origin='ai',
            speaker=src.speaker if src else '', source_ref=ref if src else '',
            source_label=src.label if src else '', evidence=quote if src else '', evidence_valid=ok,
            note=' '.join(notes)[:300]))
    return out


def persist_facts(lead, validated):
    """Replace the previous unreviewed AI facts; keep everything a salesperson confirmed, entered or rejected."""
    CustomerFact.objects.filter(lead=lead, origin='ai', confirmed_by__isnull=True).exclude(status=REJECTED).delete()
    protected = {(f.key, f.norm) for f in CustomerFact.objects.filter(lead=lead)}
    created = 0
    for d in validated:
        if (d['key'], normalize_text(d['value'])) in protected:
            continue
        CustomerFact.objects.create(lead=lead, **d)
        created += 1
    return created


def run_extraction(lead, user=None):
    """Read the lead's CRM records and store verified / assumed facts. Raises AIUnavailable on failure."""
    from .services import service_catalogue
    sources = collect_sources(lead)
    if not sources:
        return {'facts': 0, 'sources': 0, 'note': 'No CRM records with text to analyse yet.'}
    by_ref = {s.ref: s for s in sources}
    payload = {
        'today': timezone.localdate().isoformat(),
        'sis_offers': [s['name'] for s in service_catalogue()],
        'sources': [{'ref': s.ref, 'kind': s.kind, 'speaker': s.speaker, 'date': f'{s.when:%Y-%m-%d}',
                     'text': s.text} for s in sources],
    }
    data = chat_json(EXTRACTION_SYSTEM, payload, kind='extract_facts', lead=lead, user=user,
                     sources_count=len(sources))
    validated = validate_facts(data.get('facts'), by_ref)
    n = persist_facts(lead, validated)
    return {'facts': n, 'verified': sum(1 for v in validated if v['status'] == VERIFIED),
            'assumed': sum(1 for v in validated if v['status'] == ASSUMED), 'sources': len(sources)}
