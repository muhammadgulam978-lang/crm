"""
Process a LinkedIn Lead Sync webhook POST.

Two message types arrive at the same URL:
  1) {"challengeCode": "..."}  — verification handshake (answer with HMAC).
  2) A lead notification (dict or list of dicts) with `leadUrn`, `leadGenFormUrn`,
     `owner`, `campaignUrn`, `creativeUrn`, `shareUrn`.

Idempotency: LinkedInWebhookEvent.lead_urn is UNIQUE; duplicates are short-circuited.
"""
import json
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from capi import constants as C
from capi.models import LinkedInWebhookEvent, LeadTracking, PlatformConnection
from capi.services import linkedin_lead_ads as lla
from capi.services.intake import upsert_lead_from_platform

logger = logging.getLogger(__name__)


def handle_challenge(body):
    """Return (is_challenge, response_dict_or_None)."""
    if not isinstance(body, dict):
        return False, None
    code = body.get('challengeCode')
    if not code:
        return False, None
    secret = (getattr(settings, 'CAPI_LINKEDIN_CLIENT_SECRET', '')
              or getattr(settings, 'LINKEDIN_CLIENT_SECRET', ''))
    return True, {
        'challengeCode': code,
        'challengeResponse': lla.build_challenge_response(code, secret),
    }


def process_payload(raw_body):
    result = {'ok': True, 'accepted': 0, 'skipped': 0, 'errors': []}
    try:
        payload = json.loads(raw_body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        result['ok'] = False
        result['errors'].append(f'invalid_json: {exc}')
        return result

    if isinstance(payload, list):
        notifications = payload
    elif isinstance(payload, dict):
        notifications = payload.get('elements') or [payload]
    else:
        notifications = []

    for notif in notifications:
        try:
            _process_one(notif, result)
        except Exception as exc:
            logger.exception('LinkedIn lead notification failed')
            result['errors'].append(str(exc))
    return result


@transaction.atomic
def _process_one(notif, result):
    if not isinstance(notif, dict):
        return
    lead_urn = (notif.get('leadUrn') or notif.get('leadGenFormResponseUrn')
                or notif.get('responseUrn') or notif.get('id') or '')
    form_urn = (notif.get('leadGenFormUrn') or notif.get('formUrn') or '')
    owner_urn = notif.get('owner') or notif.get('ownerUrn') or ''

    if not lead_urn:
        result['errors'].append('missing_lead_urn')
        return

    linkedin_lead_id = lead_urn.rsplit(':', 1)[-1] if ':' in lead_urn else lead_urn
    form_id = form_urn.rsplit(':', 1)[-1] if ':' in form_urn else form_urn

    conn = PlatformConnection.objects.filter(platform=C.PLATFORM_LINKEDIN).first()
    if conn is None:
        result['errors'].append('linkedin_connection_missing')
        return

    try:
        event, created = LinkedInWebhookEvent.objects.get_or_create(
            lead_urn=lead_urn,
            defaults={
                
                
                'linkedin_lead_id': linkedin_lead_id,
                'form_urn': form_urn, 'form_id': form_id,
                'owner_urn': owner_urn,
                'campaign_urn': notif.get('campaignUrn') or notif.get('campaign') or '',
                'creative_urn': notif.get('creativeUrn') or '',
                'share_urn': notif.get('shareUrn') or '',
                'raw_payload': notif,
            },
        )
    except IntegrityError:
        result['skipped'] += 1
        return

    if not created and event.processed:
        result['skipped'] += 1
        return

    if not (conn.get_token() and form_urn and lead_urn):
        event.error = 'missing token or urns'
        event.save(update_fields=['error'])
        result['errors'].append('missing_token_or_urns')
        return

    ok, data = lla.fetch_lead(conn, form_urn, lead_urn)
    if not ok:
        event.error = str(data)[:500]
        event.save(update_fields=['error'])
        result['errors'].append(f'{lead_urn}: fetch_failed')
        return

    fields = lla.parse_form_response(data)
    name = fields.get('name', '') or 'Unknown'
    campaign_urn = notif.get('campaignUrn') or ''
    campaign_id = campaign_urn.rsplit(':', 1)[-1] if ':' in campaign_urn else ''

    lead, _created = upsert_lead_from_platform(

        platform=C.PLATFORM_LINKEDIN,
        name=name,
        email=fields.get('email', ''),
        phone=fields.get('contact_number', ''),
        company=fields.get('company', ''),
        job_title=fields.get('job_title', ''),      # ← NAYA
        consent=C.CONSENT_GRANTED,
        external_lead_id=linkedin_lead_id,
        campaign_id=campaign_id,
        campaign_name='',
        adset_id='', ad_id='',
        form_id=form_id,
        run_ai=True,
    )

    tracking = LeadTracking.objects.filter(lead=lead).first()
    if tracking and not tracking.external_lead_id:
        tracking.external_lead_id = lead_id
        tracking.save(update_fields=['external_lead_id'])

    event.processed = True
    event.processed_at = timezone.now()
    event.lead = lead
    event.save(update_fields=['processed', 'processed_at', 'lead'])

    result['accepted'] += 1