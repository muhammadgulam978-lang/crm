"""After-conversation assistance. The AI drafts; NOTHING is written to the CRM until the salesperson approves."""
import re
from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .extraction import numbers_supported, quote_in_source
from .llm import AIUnavailable, chat_json
from .models import VERIFIED, ConversationDraft, CustomerFact, normalize_text
from .services import _mirror_activity, primary_opportunity, recompute_rules, set_next_action

SYSTEM = """You draft post-call notes for a salesperson from THEIR raw notes of a call or meeting.
STRICT RULES
1. Use ONLY the notes. Never invent requirements, prices, dates, names, promises or next steps. If the notes do not say it, use null or an empty list.
2. Every item needs "evidence": an EXACT quote (max 200 chars) copied character for character from the notes.
3. "summary": neutral, max 100 words, only what the notes say. Do not add figures that are not in the notes.
4. "responsible" is a person named in the notes as owning the next step; otherwise null. Never choose someone yourself.
5. Dates: give "due_text" exactly as written in the notes and "due_iso" (YYYY-MM-DD) ONLY if the notes make it unambiguous, using the given meeting date to resolve words like "tomorrow". Otherwise null.
Return ONLY JSON:
{"summary":"","confirmed_requirements":[{"text","evidence"}],"decision_process":{"text","evidence"}|null,
"customer_concerns":[{"text","evidence"}],"open_questions":[{"text","evidence"}],
"agreed_next_step":{"text","evidence"}|null,"responsible":{"name","evidence"}|null,
"due":{"due_text","due_iso","evidence"}|null,
"follow_up_tasks":[{"title","owner_name","due_text","due_iso","evidence"}]}"""

RELATIVE = re.compile(r'\b(mon|tues|wednes|thurs|fri|satur|sun)day\b|tomorrow|next|week|month|kal|parson|agle', re.I)


def resolve_user(name, users):
    n = normalize_text(name)
    if not n:
        return None
    exact = [u for u in users if n in (normalize_text(u.get_username()), normalize_text(u.get_full_name()))]
    if len(exact) == 1:
        return exact[0]
    first = [u for u in users if u.first_name and normalize_text(u.first_name) == n.split(' ')[0]]
    return first[0] if len(first) == 1 else None


def _item(raw, notes):
    if not isinstance(raw, dict):
        return None
    text, ev = str(raw.get('text', '')).strip()[:400], str(raw.get('evidence', '')).strip()[:400]
    if len(text) < 3:
        return None
    ok = quote_in_source(ev, notes) and numbers_supported(text, ev)
    return {'text': text, 'evidence': ev, 'ok': bool(ok)}


def validate_draft(raw, notes, occurred):
    User = get_user_model()
    users = list(User.objects.filter(is_active=True))
    d = {'summary': '', 'summary_warning': '', 'confirmed_requirements': [], 'decision_process': None,
         'customer_concerns': [], 'open_questions': [], 'agreed_next_step': None, 'responsible': None,
         'due': None, 'follow_up_tasks': [], 'unsupported': []}
    summary = str(raw.get('summary', '')).strip()[:900]
    d['summary'] = summary
    if summary and not numbers_supported(summary, notes):
        d['summary_warning'] = 'The summary contains a figure that is not in your notes. Check it before approving.'

    def bucket(target, items):
        for it in items or []:
            x = _item(it, notes)
            if not x:
                continue
            (d[target] if x['ok'] else d['unsupported']).append({**x, 'section': target} if not x['ok'] else x)

    bucket('confirmed_requirements', raw.get('confirmed_requirements'))
    bucket('customer_concerns', raw.get('customer_concerns'))
    bucket('open_questions', raw.get('open_questions'))
    for key in ('decision_process', 'agreed_next_step'):
        x = _item(raw.get(key), notes)
        if x and x['ok']:
            d[key] = x
        elif x:
            d['unsupported'].append({**x, 'section': key})
    r = raw.get('responsible')
    if isinstance(r, dict) and quote_in_source(str(r.get('evidence', '')), notes):
        u = resolve_user(str(r.get('name', '')), users)
        d['responsible'] = {'text': str(r.get('name', ''))[:100], 'user_id': u.pk if u else None,
                            'evidence': str(r.get('evidence', ''))[:300]}
    due = raw.get('due')
    if isinstance(due, dict) and quote_in_source(str(due.get('evidence', '')), notes):
        iso = _valid_date(due.get('due_iso'), occurred)
        text = str(due.get('due_text', ''))[:80]
        d['due'] = {'text': text, 'iso': iso.isoformat() if iso else '', 'evidence': str(due.get('evidence', ''))[:300],
                    'calculated': bool(iso and RELATIVE.search(text))}
    for t in (raw.get('follow_up_tasks') or [])[:10]:
        if not isinstance(t, dict) or len(str(t.get('title', '')).strip()) < 3:
            continue
        ev = str(t.get('evidence', '')).strip()[:300]
        ok = quote_in_source(ev, notes)
        u = resolve_user(str(t.get('owner_name', '')), users) if ok else None
        iso = _valid_date(t.get('due_iso'), occurred) if ok else None
        task = {'title': str(t['title']).strip()[:200], 'owner_text': str(t.get('owner_name') or '')[:100],
                'owner_id': u.pk if u else None, 'due_text': str(t.get('due_text') or '')[:80],
                'due_iso': iso.isoformat() if iso else '', 'evidence': ev, 'ok': ok}
        (d['follow_up_tasks'] if ok else d['unsupported']).append(task if ok else {**task, 'text': task['title'], 'section': 'follow_up_tasks'})
    return d


def _valid_date(value, occurred):
    try:
        v = date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None
    return v if v >= occurred.date() else None


def create_draft(lead, user, notes, comm_type='call', occurred_at=None, meeting=None):
    occurred_at = occurred_at or timezone.now()
    opp = primary_opportunity(lead)
    draft = ConversationDraft.objects.create(
        lead=lead, opportunity=opp, meeting=meeting, comm_type=comm_type, occurred_at=occurred_at,
        raw_notes=notes.strip(), created_by=user)
    from .sources import mask
    safe_notes = mask(notes.strip())      # phone numbers / e-mails never leave the CRM
    try:
        raw = chat_json(SYSTEM, {'meeting_date': f'{occurred_at:%Y-%m-%d}', 'notes': safe_notes},
                        kind='post_call_draft', lead=lead, user=user, sources_count=1, max_tokens=1600)
        draft.draft = validate_draft(raw, safe_notes, occurred_at)
    except AIUnavailable as exc:
        draft.status = 'ai_failed'
        draft.draft = {'error': str(exc)}
    draft.save()
    return draft


@transaction.atomic
def approve_draft(draft, user, data):
    """
    `data` is the salesperson's REVIEWED version:
      summary, requirements[], decision_process, concerns[], open_questions[], next_step{action,owner,due,type},
      tasks[{title,owner,due}]
    Only now are CRM records created (communication log, facts, tasks, next action).
    """
    from leads.models import CommunicationLog, GeneralTask
    lead, opp = draft.lead, draft.opportunity or primary_opportunity(draft.lead)
    label = dict(ConversationDraft.COMM_TYPES).get(draft.comm_type, 'Conversation')
    lines = [f'SUMMARY\n{data["summary"]}']
    for title, key in (('CONFIRMED REQUIREMENTS', 'requirements'), ('CUSTOMER CONCERNS', 'concerns'),
                       ('OPEN QUESTIONS', 'open_questions')):
        if data.get(key):
            lines.append(title + '\n' + '\n'.join(f'- {x}' for x in data[key]))
    if data.get('decision_process'):
        lines.append('DECISION PROCESS\n' + data['decision_process'])
    ns = data.get('next_step') or {}
    if ns.get('action'):
        lines.append(f'AGREED NEXT STEP\n{ns["action"]}' + (f'\nOwner: {ns["owner"].get_username()}' if ns.get('owner') else '')
                     + (f'\nDue: {ns["due"]:%d %b %Y}' if ns.get('due') else ''))
    if data.get('tasks'):
        lines.append('FOLLOW-UP TASKS\n' + '\n'.join(
            f'- {t["title"]}' + (f' ({t["owner"].get_username()})' if t.get('owner') else '')
            + (f' - due {t["due"]:%d %b %Y}' if t.get('due') else '') for t in data['tasks']))
    log = CommunicationLog.objects.create(
        comm_type=draft.comm_type, direction='internal', subject=f'{label} notes {draft.occurred_at:%d %b %Y} (approved)',
        body='\n\n'.join(lines), created_by=user, lead=lead, opportunity=opp)

    evidence = {normalize_text(i['text']): i['evidence'] for k in ('confirmed_requirements', 'customer_concerns', 'open_questions')
                for i in (draft.draft.get(k) or [])}
    if draft.draft.get('decision_process'):
        evidence[normalize_text(draft.draft['decision_process']['text'])] = draft.draft['decision_process']['evidence']

    def fact(key, text):
        ev = evidence.get(normalize_text(text), '')
        CustomerFact.objects.create(
            lead=lead, key=key, value=text.strip()[:400], status=VERIFIED, origin='post_call', speaker='staff',
            source_ref=f'comm:{log.pk}', source_label=f'Approved call notes {draft.occurred_at:%d %b %Y}',
            evidence=ev, evidence_valid=bool(ev), confirmed_by=user, confirmed_at=timezone.now(), created_by=user)

    for text in data.get('requirements', []):
        fact('requirement', text)
    for text in data.get('concerns', []):
        fact('objection', text)
    for text in data.get('open_questions', []):
        fact('open_question', text)
    if data.get('decision_process'):
        fact('decision_process', data['decision_process'])

    for t in data.get('tasks', []):
        GeneralTask.objects.create(title=t['title'][:200], description=f'From approved {label.lower()} notes ({draft.occurred_at:%d %b %Y}).',
                                   assigned_to=t.get('owner') or user, created_by=user, due_date=t.get('due'),
                                   lead=lead, opportunity=opp)

    if ns.get('action'):
        proposed = draft.draft.get('agreed_next_step') or {}
        resp = draft.draft.get('responsible') or {}
        due = draft.draft.get('due') or {}
        ai_rec = {'action_type': 'other', 'action': proposed.get('text', ''), 'owner_id': resp.get('user_id'),
                  'due_date': due.get('iso') or None} if proposed else {}
        set_next_action(lead, user, action_type=ns.get('type') or 'other', action=ns['action'], owner=ns.get('owner') or user,
                        due_date=ns.get('due'), reason=ns.get('reason', ''), origin='post_conversation', ai_rec=ai_rec, opportunity=opp)

    draft.final = {k: (v if not hasattr(v, 'pk') else v.pk) for k, v in data.items() if k not in ('next_step', 'tasks')}
    draft.final['next_step'] = {'action': ns.get('action', ''), 'owner': getattr(ns.get('owner'), 'pk', None),
                                'due': ns['due'].isoformat() if ns.get('due') else None}
    draft.final['tasks'] = [{'title': t['title'], 'owner': getattr(t.get('owner'), 'pk', None),
                             'due': t['due'].isoformat() if t.get('due') else None} for t in data.get('tasks', [])]
    draft.status, draft.approved_by, draft.approved_at, draft.communication_log_id = 'approved', user, timezone.now(), log.pk
    draft.save()
    _mirror_activity(user, lead, f'{label} notes approved and saved ({len(data.get("tasks", []))} follow-up task(s))')
    recompute_rules(lead, user)
    return log
