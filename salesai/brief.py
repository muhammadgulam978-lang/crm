"""Pre-call brief. Assembled from verified CRM facts; the AI only helps summarise earlier communication."""
from django.utils import timezone

from . import portfolio, rules
from .extraction import numbers_supported
from .llm import AIUnavailable, chat_json
from .models import ASSUMED, VERIFIED, CallBrief, CustomerFact, LeadInsight
from .services import build_state, primary_opportunity, recompute_rules
from .sources import collect_sources

SYSTEM = """You summarise earlier communication with a sales lead for the salesperson.
STRICT RULES: use ONLY the provided sources; one neutral sentence per source (max 35 words); never add facts, prices,
dates, promises or opinions that are not written in that source; do not guess what the customer feels or wants.
Return ONLY JSON: {"items":[{"source_ref":"...","summary":"..."}]}"""


def _previous_communication(lead, user, use_ai):
    sources = [s for s in collect_sources(lead) if s.kind in ('communication', 'meeting', 'email', 'chat', 'followup', 'proposal', 'quotation')][:6]
    by_ref = {s.ref: s for s in sources}
    rows, ai_used = [], False
    if use_ai and sources:
        try:
            data = chat_json(SYSTEM, {'sources': [{'ref': s.ref, 'kind': s.kind, 'speaker': s.speaker,
                                                   'date': f'{s.when:%Y-%m-%d}', 'text': s.text} for s in sources]},
                             kind='call_brief', lead=lead, user=user, sources_count=len(sources), max_tokens=900)
            for it in data.get('items') or []:
                s = by_ref.get(str(it.get('source_ref')))
                text = str(it.get('summary', '')).strip()[:300]
                if s and text and numbers_supported(text, s.text):
                    rows.append({'ref': s.ref, 'date': f'{s.when:%d %b %Y}', 'label': s.label, 'speaker': s.speaker,
                                 'text': text, 'ai': True})
            ai_used = bool(rows)
        except AIUnavailable:
            pass
    if not rows:  # deterministic fallback: the record's own words, truncated
        for s in sources:
            rows.append({'ref': s.ref, 'date': f'{s.when:%d %b %Y}', 'label': s.label, 'speaker': s.speaker,
                         'text': (s.text[:200] + ('…' if len(s.text) > 200 else '')).replace('\n', ' '), 'ai': False})
    return rows, ai_used


def generate_brief(lead, user, use_ai=True):
    insight = LeadInsight.objects.filter(lead=lead).first() or recompute_rules(lead, user)
    state = build_state(lead)
    q, rec = insight.qualification or {}, insight.recommendation or {}
    facts = CustomerFact.objects.filter(lead=lead).exclude(status='rejected')
    by = lambda key, status: [f for f in facts if f.key == key and f.status == status]
    fmt = lambda f: {'text': f.value, 'source': f.source_label or 'Entered by staff', 'evidence': f.evidence,
                     'how': f.verified_how}
    opp = primary_opportunity(lead)
    contact = lead.contacts.select_related('company').first()
    company = (opp.company if opp and opp.company else (contact.company if contact else None))

    match = insight.offer_match or {}
    matched = rules.match_services(state)
    objections = [{'text': f.value, 'origin': 'Raised by this customer' if f.status == VERIFIED else 'Assumption (unconfirmed)',
                   'source': f.source_label} for f in facts if f.key == 'objection']
    for s in matched:
        objections += [{'text': o, 'origin': f'Approved SIS note for {s["name"]}', 'source': ''} for o in s['objections']]
    for row in portfolio.common_objections(limit=3, exclude_lead=lead.pk):
        objections.append({'text': row['label'], 'origin': f'Raised by {row["count"]} other leads', 'source': ''})

    previous, ai_used = _previous_communication(lead, user, use_ai)
    open_asked = [fmt(f) for f in facts if f.key == 'open_question' and not f.resolved]
    missing = [{'label': q['fields'][k]['label'], 'field': k,
                'suggested_question': rules.QUESTION_TEXT[k]} for k in (q.get('missing') or [])]
    warnings = []
    if not by('need', VERIFIED):
        warnings.append('No verified need is recorded. Confirm the need before proposing anything.')
    if insight.priority and insight.priority.get('level') == 'insufficient':
        warnings.append('Not enough verified information to recommend a sales priority.')
    if not matched:
        warnings.append('No approved SIS offer matched the verified need. Choose the solution yourself or run a discovery.')
    for s in matched:
        gaps = [lbl for lbl, val in (('features', s['features']), ('pricing', s['pricing_note']), ('delivery timeline', s['timeline_note']))
                if not val]
        if gaps:
            warnings.append(f'{s["name"]}: {", ".join(gaps)} not in the knowledge base - do not quote them; confirm with the Sales Lead.')

    content = {
        'customer': {'lead': lead.name, 'company': company.name if company else '', 'contact': contact.get_full_name() if contact else '',
                     'country': lead.country or '', 'source': lead.get_lead_source_display(),
                     'stage': (f'Opportunity: {opp.get_stage_display()}' if opp else f'Lead: {lead.get_status_display()}')},
        'stated_need': {'verified': [fmt(f) for f in by('need', VERIFIED)], 'assumed': [fmt(f) for f in by('need', ASSUMED)]},
        'previous_communication': previous,
        'recommended_offer': {'label': (rec.get('product') or {}).get('label', ''), 'status': match.get('status', ''),
                              'why': match.get('why', []), 'main_problem': (match.get('main_problem') or {}).get('text', '')},
        'relevant_solutions': [{'name': s['name'], 'label': s['label'], 'description': s['description'], 'features': s['features'],
                                'pricing_note': s['pricing_note'], 'timeline_note': s['timeline_note'],
                                'guarantees_note': s['guarantees_note'], 'cases': s['cases'],
                                'discovery_questions': s['discovery_questions']} for s in matched],
        'known_requirements': [fmt(f) for f in by('requirement', VERIFIED)],
        'assumed_requirements': [fmt(f) for f in by('requirement', ASSUMED)],
        'open_questions': {'customer_asked': open_asked, 'missing_information': missing},
        'possible_objections': objections,
        'recommended_next_step': {'action': rec.get('action', ''), 'reason': rec.get('reason', ''), 'code': rec.get('code', '')},
        'warnings': warnings,
    }
    return CallBrief.objects.create(lead=lead, opportunity=opp, content=content, ai_used=ai_used, generated_by=user)
