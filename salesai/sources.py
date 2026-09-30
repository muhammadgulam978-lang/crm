"""
Collects the CRM records the assistant is allowed to read, as numbered sources.

Deliberately EXCLUDED: text that the AI itself wrote (Lead.ai_score_reason, Meeting.ai_summary,
Proposal.generated_proposal, other AI drafts) - otherwise the AI would "verify" its own output.
E-mail addresses and phone numbers are masked before anything is sent to the language model.
"""
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

MAX_SOURCES = 30
MAX_CHARS_PER_SOURCE = 1500
MAX_TOTAL_CHARS = 16000

_EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
_PHONE = re.compile(r'(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)')


def mask(text: str) -> str:
    text = _EMAIL.sub('[email]', text or '')
    return _PHONE.sub('[phone]', text)


@dataclass
class Source:
    ref: str
    kind: str
    speaker: str      # customer | staff | unspecified
    when: datetime
    label: str
    text: str


def _s(ref, kind, speaker, when, label, text):
    text = mask((text or '').strip())
    if not text:
        return None
    return Source(ref, kind, speaker, when or timezone.now(), label, text[:MAX_CHARS_PER_SOURCE])


def collect_sources(lead):
    from leads.models import (CommunicationLog, Contact, EmailLog, FollowUp, Meeting,
                              Opportunity, Proposal, Quotation)
    out = []
    add = lambda s: out.append(s) if s else None

    add(_s(f'enquiry:{lead.pk}', 'enquiry', 'unspecified', lead.created_at,
           f'Enquiry / lead notes ({lead.get_lead_source_display()})', lead.detail))

    opps = list(Opportunity.objects.filter(lead=lead))
    opp_ids = [o.pk for o in opps]
    contact_ids = list(Contact.objects.filter(lead=lead).values_list('pk', flat=True))

    from django.db.models import Q
    comms = CommunicationLog.objects.filter(
        Q(lead=lead) | Q(opportunity_id__in=opp_ids) | Q(contact_id__in=contact_ids)).distinct()
    for c in comms.order_by('-created_at')[:25]:
        speaker = 'customer' if c.direction == 'inbound' else 'staff'
        add(_s(f'comm:{c.pk}', 'communication', speaker, c.created_at,
               f'{c.get_comm_type_display()} ({c.get_direction_display().lower()}) {c.created_at:%d %b %Y}',
               f'{c.subject or ""}\n{c.body}'))

    for m in Meeting.objects.filter(Q(lead=lead) | Q(opportunity_id__in=opp_ids)).distinct().order_by('-scheduled_at')[:10]:
        text = '\n'.join(x for x in (m.agenda and f'Agenda: {m.agenda}', m.notes and f'Notes: {m.notes}',
                                     m.action_items and f'Action items: {m.action_items}') if x)
        add(_s(f'meeting:{m.pk}', 'meeting', 'staff', m.scheduled_at,
               f'Meeting notes "{m.title}" {m.scheduled_at:%d %b %Y}', text))

    for f in FollowUp.objects.filter(lead=lead).order_by('-created_at')[:10]:
        add(_s(f'followup:{f.pk}', 'followup', 'staff', f.created_at,
               f'Follow-up note {f.follow_up_date:%d %b %Y}', f.notes))

    for e in EmailLog.objects.filter(lead=lead).order_by('-created_at')[:10]:
        speaker = 'customer' if e.direction == 'inbound' else 'staff'
        add(_s(f'email:{e.pk}', 'email', speaker, e.created_at,
               f'Email ({e.direction}) {e.created_at:%d %b %Y}', f'{e.subject}\n{e.message}'))

    # WhatsApp chatbot conversation (customer messages only carry customer statements)
    try:
        from chatbot.models import ChatMessage, ChatSession
        from chatbot.services.utils import normalize_phone
        variants = {lead.contact_number, normalize_phone(lead.contact_number)}
        for s in ChatSession.objects.filter(phone_number__in=[v for v in variants if v]):
            for msg in ChatMessage.objects.filter(session=s).order_by('-created_at')[:25]:
                who = 'customer' if msg.direction == 'inbound' else 'staff'
                add(_s(f'chat:{msg.pk}', 'chat', who, msg.created_at,
                       f'WhatsApp chat ({msg.direction}) {msg.created_at:%d %b %Y}', msg.message))
    except Exception:
        pass

    # proposal feedback: only what people wrote (notes, status, reasons), never generated proposal text
    for p in Proposal.objects.filter(lead=lead).order_by('-created_at')[:5]:
        add(_s(f'proposal:{p.pk}', 'proposal', 'staff', p.created_at,
               f'Proposal notes "{p.title}" (status {p.status})', p.manual_notes))
    for q in Quotation.objects.filter(Q(lead=lead) | Q(opportunity_id__in=opp_ids)).distinct().order_by('-created_at')[:5]:
        add(_s(f'quotation:{q.pk}', 'quotation', 'staff', q.created_at,
               f'Quotation "{q.title}" (status {q.status})', q.notes))
    for o in opps:
        text = '\n'.join(x for x in (o.description and f'Description: {o.description}',
                                     o.lost_reason and f'Lost reason: {o.lost_reason}') if x)
        add(_s(f'opp:{o.pk}', 'opportunity', 'staff', o.updated_at, f'Opportunity "{o.title}" ({o.stage})', text))
    for c in Contact.objects.filter(lead=lead)[:5]:
        add(_s(f'contact:{c.pk}', 'contact', 'staff', c.created_at, f'Contact note {c.get_full_name()}', c.notes))

    # campaign result (structured, from the Conversion API module when it is installed)
    try:
        from capi.models import LeadTracking
        t = LeadTracking.objects.filter(lead=lead).first()
        if t and (t.campaign_name or t.campaign_id):
            add(_s(f'campaign:{lead.pk}', 'campaign', 'unspecified', t.created_at if hasattr(t, 'created_at') else None,
                   'Campaign attribution', f'Lead came from campaign "{t.campaign_name or t.campaign_id}" on {t.platform or "an ad platform"}.'))
    except Exception:
        pass

    out.sort(key=lambda s: s.when, reverse=True)
    picked, total = [], 0
    for s in out[:MAX_SOURCES]:
        if total + len(s.text) > MAX_TOTAL_CHARS:
            continue
        picked.append(s)
        total += len(s.text)
    return picked


def fingerprint(lead) -> str:
    """Changes whenever a record the assistant reads changes -> tells us the AI review is stale."""
    from leads.models import CommunicationLog, EmailLog, FollowUp, Meeting, Opportunity
    parts = [str(lead.updated_at), lead.detail or '']
    for model, field in ((CommunicationLog, 'created_at'), (EmailLog, 'created_at'), (FollowUp, 'created_at'),
                         (Meeting, 'created_at'), (Opportunity, 'updated_at')):
        qs = model.objects.filter(lead=lead)
        last = qs.order_by(f'-{field}').values_list(field, flat=True).first()
        parts.append(f'{model.__name__}:{qs.count()}:{last}')
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()
