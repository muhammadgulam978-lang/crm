"""Lead direction (Meta / LinkedIn Lead Sync -> CRM). Deduplicates instead of creating
a second lead when the same person interacts with several campaigns.

Also writes a meaningful CRM timeline entry (CommunicationLog) and an audit entry
(ActivityLog) for every imported lead."""
import json
import logging
from django.db import transaction
from leads.models import ActivityLog, CommunicationLog, Lead
from .. import constants as C
from ..models import LeadTouchpoint, LeadTracking
from . import identifiers as ids

logger = logging.getLogger(__name__)

# Map our platform constant -> Lead.lead_source choice
# SOURCE_BY_PLATFORM = {
#     'meta': 'facebook',
#     'linkedin': 'linkedin',
# }
PLATFORM_DISPLAY = {
    'meta': 'Meta',
    'linkedin': 'LinkedIn',
}
TOUCH_FIELDS = ('campaign_id', 'campaign_name', 'adset_id', 'ad_id', 'form_id', 'external_lead_id')
CLICK_FIELDS = ('fbc', 'fbp', 'li_fat_id', 'client_ip', 'user_agent')


def find_existing_lead(platform, email='', phone='', external_lead_id=''):
    if external_lead_id:
        tp = LeadTouchpoint.objects.filter(platform=platform, external_lead_id=external_lead_id).first()
        if tp:
            return tp.lead
    em = ids.normalize_email(email)
    if em:
        lead = Lead.objects.filter(email__iexact=em).order_by('id').first()
        if lead:
            return lead
    ph = ids.normalize_phone(phone)
    if ph:
        tail = ph[-9:]
        for lead in Lead.objects.filter(contact_number__icontains=tail[-7:]).order_by('id'):
            other = ids.normalize_phone(lead.contact_number)
            if other and other[-9:] == tail:
                return lead
    return None


def _timeline_body(platform, touch, extra_fields):
    """Compose a human-readable body for the lead's timeline."""
    # lines = [f'Lead received from {platform.title()} Lead Ads.']
    
    display = PLATFORM_DISPLAY.get(platform, platform.title())
    lines = [f'Lead received from {display} Lead Ads.']
    if touch.get('campaign_name') or touch.get('campaign_id'):
        camp = touch.get('campaign_name') or ''
        cid = touch.get('campaign_id') or ''
        lines.append(f'Campaign: {camp} {("(" + cid + ")") if cid else ""}'.strip())
    if touch.get('adset_id'):
        lines.append(f'Ad set: {touch["adset_id"]}')
    if touch.get('ad_id'):
        lines.append(f'Ad: {touch["ad_id"]}')
    if touch.get('form_id'):
        lines.append(f'Form: {touch["form_id"]}')
    if touch.get('external_lead_id'):
        lines.append(f'Platform lead ID: {touch["external_lead_id"]}')
    for key, value in (extra_fields or {}).items():
        if value:
            lines.append(f'{key}: {value}')
    return '\n'.join(lines)


@transaction.atomic
def upsert_lead_from_platform(platform, *, name='', email='', phone='', company='',
                              job_title='', consent=None, run_ai=True, **extra):
    """Returns (lead, created). `extra` may hold TOUCH_FIELDS, CLICK_FIELDS,
    page_id, adset_name, ad_name, form_answers and any other raw form answers."""
    touch = {k: (extra.get(k) or '') for k in TOUCH_FIELDS}
    clicks = {k: extra.get(k) for k in CLICK_FIELDS if extra.get(k)}
    form_answers = extra.get('form_answers') or {}
    adset_name = extra.get('adset_name') or ''
    ad_name = extra.get('ad_name') or ''

    lead = find_existing_lead(platform, email, phone, touch['external_lead_id'])
    created = lead is None
    if created:
        detail_parts = []
        if company:
            detail_parts.append(f'Company: {company}')
        if job_title:
            detail_parts.append(f'Job title: {job_title}')
        lead = Lead.objects.create(
            name=name or 'Unknown',
            contact_number=phone or '',
            email=ids.normalize_email(email),
            lead_source=SOURCE_BY_PLATFORM.get(platform, 'advertisement'),
            detail='\n'.join(detail_parts) or None,
        )
    else:
        changed = []
        if email and not lead.email and ids.normalize_email(email):
            lead.email = ids.normalize_email(email); changed.append('email')
        if phone and not lead.contact_number:
            lead.contact_number = phone; changed.append('contact_number')
        if changed:
            lead.save(update_fields=changed)

    LeadTouchpoint.objects.create(lead=lead, platform=platform, **touch)

    tracking, _ = LeadTracking.objects.get_or_create(lead=lead)
    tracking.platform = platform
    for k, v in touch.items():
        if v:
            setattr(tracking, k, v)
    for k, v in clicks.items():
        setattr(tracking, k, v)
    if consent in (C.CONSENT_GRANTED, C.CONSENT_DENIED):
        tracking.consent_status = consent
    tracking.save()

    # ---- CRM TIMELINE: CommunicationLog (shows on lead detail page) ----
    try:
        extra_lines = {}
        if job_title:
            extra_lines['Job title'] = job_title
        if company:
            extra_lines['Company'] = company
        if adset_name:
            extra_lines['Ad set name'] = adset_name
        if ad_name:
            extra_lines['Ad name'] = ad_name

        body = _timeline_body(platform, touch, extra_lines)
        if form_answers:
            body += '\n\nForm answers:'
            for q, a in form_answers.items():
                if a and q != '_raw':
                    body += f'\n  {q}: {a}'

        CommunicationLog.objects.create(
            comm_type='note',
            direction='inbound',
            subject=f'Lead imported from {PLATFORM_DISPLAY.get(platform, platform.title())} Lead Ads',
            body=body,
            lead=lead,
        )
    except Exception:
        logger.exception('Failed to write CommunicationLog for lead %s', lead.pk)

    # ---- CRM AUDIT: ActivityLog (structured audit trail) ----
    try:
        ActivityLog.objects.create(
            action='created',
            entity_type='Lead',
            entity_id=lead.pk,
            entity_name=lead.name,
            # description=(
            #     f'Lead {"created" if created else "updated"} from {platform.title()} '
            #     f'Lead Ads (form {touch.get("form_id") or "n/a"}, '
            #     f'campaign {touch.get("campaign_id") or "n/a"}).'
            # ),
            
            description=(
                f'Lead {"created" if created else "updated"} from '
                f'{PLATFORM_DISPLAY.get(platform, platform.title())} '
                f'Lead Ads (form {touch.get("form_id") or "n/a"}, '
                f'campaign {touch.get("campaign_id") or "n/a"}).'
            ),
        )
    except Exception:
        logger.exception('Failed to write ActivityLog for lead %s', lead.pk)

    # ---- Existing AI follow-up ----
    if created and run_ai:
        try:
            from leads.duplicate_detector import check_and_mark_duplicate
            if not check_and_mark_duplicate(lead):
                from leads.ai_assignment import assign_lead_with_ai
                assign_lead_with_ai(lead)
            from leads.ai_scorer import update_lead_score
            update_lead_score(lead)
        except Exception:
            logger.exception('AI post-processing failed for lead %s', lead.pk)
    return lead, created