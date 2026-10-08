"""
Process a Meta webhook POST:
  raw JSON -> validate signature -> per change: fetch lead -> upsert Lead.

Idempotency:
  - MetaWebhookEvent.leadgen_id has a UNIQUE constraint.
  - We wrap the whole thing in a transaction and use get_or_create on leadgen_id.
  - Any duplicate POST is short-circuited.
"""
import json
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from leads.models import Lead
from capi import constants as C
from capi.models import MetaWebhookEvent, LeadTracking, PlatformConnection
from capi.services import meta_lead_ads as mla
from capi.services.intake import upsert_lead_from_platform

logger = logging.getLogger(__name__)


def process_payload(raw_body, signature_header):
    """
    Returns dict: {ok, accepted, skipped, errors}
    This is called by the POST view AFTER reading request.body once.
    """
    result = {'ok': True, 'accepted': 0, 'skipped': 0, 'errors': []}

    app_secret = getattr(settings, 'META_APP_SECRET', '')
    if not mla.verify_signature(raw_body, signature_header, app_secret):
        result['ok'] = False
        result['errors'].append('signature_mismatch')
        return result

    try:
        payload = json.loads(raw_body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        result['ok'] = False
        result['errors'].append(f'invalid_json: {exc}')
        return result

    if payload.get('object') != 'page':
        result['ok'] = False
        result['errors'].append('unexpected_object')
        return result

    for entry in payload.get('entry', []):
        page_id = str(entry.get('id') or '')
        conn = PlatformConnection.objects.filter(
            platform=C.PLATFORM_META, page_id=page_id
        ).first()
        if conn is None:
            result['errors'].append(f'unknown_page:{page_id}')
            continue

        for change in entry.get('changes', []):
            if change.get('field') != 'leadgen':
                continue
            value = change.get('value') or {}
            leadgen_id = str(value.get('leadgen_id') or '')
            if not leadgen_id:
                continue
            try:
                _process_one(value, conn, result)
            except Exception as exc:
                logger.exception('leadgen %s failed', leadgen_id)
                result['errors'].append(f'{leadgen_id}: {exc}')
    return result


@transaction.atomic
def _process_one(value, conn, result):
    leadgen_id = str(value.get('leadgen_id') or '')
    page_id = str(value.get('page_id') or conn.page_id or '')

    # Idempotency: unique on leadgen_id
    try:
        event, created = MetaWebhookEvent.objects.get_or_create(
            leadgen_id=leadgen_id,
            defaults={
                'page_id': page_id,
                'form_id': str(value.get('form_id') or ''),
                'ad_id': str(value.get('ad_id') or ''),
                'adset_id': str(value.get('adgroup_id') or value.get('adset_id') or ''),
                'campaign_id': str(value.get('campaign_id') or ''),
                'raw_payload': value,
            },
        )
    except IntegrityError:
        result['skipped'] += 1
        return

    if not created and event.processed:
        result['skipped'] += 1
        return

    # Fetch full lead from Graph API
    page_token = conn.get_page_token()
    if not page_token:
        event.error = 'no page access token configured'
        event.save(update_fields=['error'])
        result['errors'].append(f'{leadgen_id}: no page token')
        return

    ok, data = mla.fetch_lead(leadgen_id, page_token,
                              api_version=getattr(settings, 'META_GRAPH_VERSION', 'v21.0'))
    if not ok:
        event.error = str(data)[:500]
        event.save(update_fields=['error'])
        result['errors'].append(f'{leadgen_id}: graph_fetch_failed')
        return

    # Parse fields
    fields = mla.parse_field_data(data.get('field_data', []))
    lead, lead_created = upsert_lead_from_platform(
        platform=C.PLATFORM_META,
        name=fields.get('name', ''),
        email=fields.get('email', ''),
        phone=fields.get('contact_number', ''),
        company=fields.get('company', ''),
        consent=C.CONSENT_GRANTED,  # Facebook lead form submission = consent to contact
        external_lead_id=leadgen_id,
        campaign_id=str(data.get('campaign_id') or event.campaign_id or ''),
        campaign_name='',
        adset_id=str(data.get('adset_id') or event.adset_id or ''),
        ad_id=str(data.get('ad_id') or event.ad_id or ''),
        form_id=str(data.get('form_id') or event.form_id or ''),
        run_ai=True,
    )

    # Save meta_leadgen_id on LeadTracking (idempotency second layer)
    tracking = LeadTracking.objects.filter(lead=lead).first()
    if tracking and not tracking.meta_leadgen_id:
        tracking.meta_leadgen_id = leadgen_id
        tracking.save(update_fields=['meta_leadgen_id'])

    event.processed = True
    event.processed_at = timezone.now()
    event.lead = lead
    event.save(update_fields=['processed', 'processed_at', 'lead'])

    result['accepted'] += 1