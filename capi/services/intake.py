"""Lead direction (Meta / LinkedIn Lead Sync -> CRM). Deduplicates instead of creating
a second lead when the same person interacts with several campaigns, and replays of the
same Meta lead ID are no-ops."""
import logging

from django.db import connection, transaction

from leads.models import Lead

from .. import constants as C
from ..models import LeadTouchpoint, LeadTracking
from . import crm_hooks
from . import identifiers as ids

logger = logging.getLogger(__name__)
SOURCE_BY_PLATFORM = {'meta': 'facebook', 'linkedin': 'advertisement'}
PLATFORM_LABEL = {'meta': 'Facebook/Meta Lead Ad', 'linkedin': 'LinkedIn Lead Gen'}
TOUCH_FIELDS = ('campaign_id', 'campaign_name', 'adset_id', 'adset_name', 'ad_id', 'ad_name',
                'page_id', 'form_id', 'external_lead_id')
CLICK_FIELDS = ('fbc', 'fbp', 'li_fat_id', 'client_ip', 'user_agent')
STANDARD_ANSWER_KEYS = {'full_name', 'name', 'first_name', 'last_name', 'email', 'email_address',
                        'phone_number', 'phone', 'mobile', 'company_name', 'company'}


def _intake_lock():
    """Serialise intake on PostgreSQL so two simultaneous submissions from the same person
    cannot both pass the 'does this lead exist?' check. Released at transaction end."""
    if connection.vendor == 'postgresql':
        with connection.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(%s)', [7_204_118_001])


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


def _pretty(key):
    return key.replace('_', ' ').strip().capitalize()


def format_answers(answers, skip_standard=False):
    lines = []
    for key, value in (answers or {}).items():
        if skip_standard and key in STANDARD_ANSWER_KEYS:
            continue
        if value not in ('', None):
            lines.append(f'{_pretty(key)}: {value}')
    return '\n'.join(lines)


def _activity_text(platform, created, touch, answers, company):
    label = PLATFORM_LABEL.get(platform, platform)
    title = (f'Lead created from {label}' if created
             else f'Existing lead re-engaged via {label}')
    parts = [f'{k}: {touch[k]}' for k in ('campaign_name', 'campaign_id', 'adset_name', 'adset_id',
                                         'ad_name', 'ad_id', 'form_id', 'page_id', 'external_lead_id')
             if touch.get(k)]
    if company:
        parts.append(f'company: {company}')
    body = '\n'.join(parts)
    answered = format_answers(answers)
    if answered:
        body += '\n\nForm answers:\n' + answered
    return title, body


@transaction.atomic
def upsert_lead_from_platform(platform, *, name='', email='', phone='', company='',
                              consent=None, run_ai=True, **extra):
    """Returns (lead, created). `extra` may hold TOUCH_FIELDS, CLICK_FIELDS and form_answers.
    Replaying the same external_lead_id returns the original lead without touching anything."""
    touch = {k: (extra.get(k) or '') for k in TOUCH_FIELDS}
    clicks = {k: extra.get(k) for k in CLICK_FIELDS if extra.get(k)}
    answers = extra.get('form_answers') or {}

    _intake_lock()

    if touch['external_lead_id']:
        seen = LeadTouchpoint.objects.filter(
            platform=platform, external_lead_id=touch['external_lead_id']).select_related('lead').first()
        if seen:
            return seen.lead, False

    lead = find_existing_lead(platform, email, phone, touch['external_lead_id'])
    created = lead is None
    if created:
        detail_parts = []
        if company:
            detail_parts.append(f'Company: {company}')
        extra_answers = format_answers(answers, skip_standard=True)
        if extra_answers:
            detail_parts.append(f'{PLATFORM_LABEL.get(platform, platform)} form answers:\n{extra_answers}')
        lead = Lead.objects.create(
            name=name or 'Unknown', contact_number=phone or '', email=ids.normalize_email(email) or '',
            lead_source=SOURCE_BY_PLATFORM.get(platform, 'advertisement'),
            detail='\n\n'.join(detail_parts) or None)
    else:
        changed = []
        if email and not lead.email and ids.normalize_email(email):
            lead.email = ids.normalize_email(email); changed.append('email')
        if phone and not lead.contact_number:
            lead.contact_number = phone; changed.append('contact_number')
        if changed:
            lead.save(update_fields=changed)

    LeadTouchpoint.objects.create(lead=lead, platform=platform, form_answers=answers, **touch)

    tracking, _ = LeadTracking.objects.get_or_create(lead=lead)
    tracking.platform = platform                       # latest touch drives attribution
    for k, v in touch.items():
        if v:
            setattr(tracking, k, v)
    if platform == C.PLATFORM_META and touch['external_lead_id'] and not tracking.meta_leadgen_id:
        tracking.meta_leadgen_id = touch['external_lead_id']
    for k, v in clicks.items():
        setattr(tracking, k, v)
    if consent in (C.CONSENT_GRANTED, C.CONSENT_DENIED):
        tracking.consent_status = consent
    tracking.save()

    title, body = _activity_text(platform, created, touch, answers, company)
    crm_hooks.log_activity(lead, title, body)

    if run_ai:
        lead_id = lead.pk
        transaction.on_commit(lambda: _post_import(lead_id, created, touch))
    return lead, created


def _post_import(lead_id, created, touch):
    """Reuse the CRM's own AI pipeline + follow-up hooks AFTER the import has committed.
    Never lets a failure here affect the imported lead."""
    lead = Lead.objects.filter(pk=lead_id).first()
    if lead is None:
        return
    try:
        if created:
            from leads.duplicate_detector import check_and_mark_duplicate
            if not check_and_mark_duplicate(lead):
                from leads.ai_assignment import assign_lead_with_ai
                assign_lead_with_ai(lead)
        from leads.ai_scorer import update_lead_score
        update_lead_score(lead)
    except Exception:
        logger.exception('AI post-processing failed for lead %s', lead_id)
    crm_hooks.run_post_import(lead, created, touch)
