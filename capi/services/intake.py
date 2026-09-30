"""Lead direction (Meta / LinkedIn Lead Sync -> CRM). Deduplicates instead of creating
a second lead when the same person interacts with several campaigns."""
import logging

from django.db import transaction
from django.utils import timezone

from leads.models import Lead

from .. import constants as C
from ..models import LeadTouchpoint, LeadTracking
from . import identifiers as ids

logger = logging.getLogger(__name__)
SOURCE_BY_PLATFORM = {'meta': 'facebook', 'linkedin': 'advertisement'}
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


@transaction.atomic
def upsert_lead_from_platform(platform, *, name='', email='', phone='', company='',
                              consent=None, run_ai=True, **extra):
    """Returns (lead, created). `extra` may hold TOUCH_FIELDS and CLICK_FIELDS."""
    touch = {k: (extra.get(k) or '') for k in TOUCH_FIELDS}
    clicks = {k: extra.get(k) for k in CLICK_FIELDS if extra.get(k)}

    lead = find_existing_lead(platform, email, phone, touch['external_lead_id'])
    created = lead is None
    if created:
        detail = f'Company: {company}' if company else ''
        lead = Lead.objects.create(
            name=name or 'Unknown', contact_number=phone or '', email=ids.normalize_email(email),
            lead_source=SOURCE_BY_PLATFORM.get(platform, 'advertisement'), detail=detail or None)
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
    tracking.platform = platform                       # latest touch drives attribution
    for k, v in touch.items():
        if v:
            setattr(tracking, k, v)
    for k, v in clicks.items():
        setattr(tracking, k, v)
    if consent in (C.CONSENT_GRANTED, C.CONSENT_DENIED):
        tracking.consent_status = consent
    tracking.save()

    if created and run_ai:
        # Reuse the CRM's own AI pipeline; never let it break intake.
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
