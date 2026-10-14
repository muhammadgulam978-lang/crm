# """Meta Lead Ads webhook processing.

#   signed POST -> store one MetaWebhookEvent per leadgen_id (UNIQUE) -> fetch the lead from
#   the Graph API -> upsert the CRM lead.

# Reliability model
#   * The event row is committed BEFORE any Graph/CRM work, so a notification is never lost.
#   * Replays are idempotent: UNIQUE(leadgen_id) on the event, a `processed` check under a row
#     lock, and the touchpoint lookup in upsert_lead_from_platform.
#   * Anything that fails (Graph down, token expired, CRM error) is retried with back-off by
#     `retry_due()` (run by the process_conversion_queue command); after
#     WEBHOOK_MAX_ATTEMPTS the event is marked gave_up and shows in the admin UI for a manual retry.
#   * Meta always gets HTTP 200 for a correctly SIGNED request - retries are ours to manage.
# """
# import json
# import logging
# from datetime import timedelta

# from django.db import IntegrityError, transaction
# from django.db.models import Q
# from django.utils import timezone

# from capi import conf
# from capi import constants as C
# from capi.models import MetaWebhookEvent, PlatformConnection
# from capi.services import meta_lead_ads as mla
# from capi.services.intake import upsert_lead_from_platform

# logger = logging.getLogger(__name__)


# # ---------------------------------------------------------------- receive

# def process_payload(raw_body, signature_header):
#     """Returns {ok, accepted, skipped, queued, errors}. ok=False only for an unsigned /
#     malformed request (the view answers 401); everything else is stored and retried."""
#     result = {'ok': True, 'accepted': 0, 'skipped': 0, 'queued': 0, 'errors': []}

#     if not mla.verify_signature(raw_body, signature_header, conf.get('META_APP_SECRET')):
#         result.update(ok=False, errors=['signature_mismatch'])
#         return result
#     try:
#         payload = json.loads(raw_body.decode('utf-8'))
#     except (ValueError, UnicodeDecodeError):
#         result.update(ok=False, errors=['invalid_json'])
#         return result
#     if payload.get('object') != 'page':
#         result.update(ok=False, errors=['unexpected_object'])
#         return result

#     for entry in payload.get('entry', []):
#         page_id = str(entry.get('id') or '')
#         conn = PlatformConnection.objects.filter(platform=C.PLATFORM_META, page_id=page_id).first()
#         if conn is None:
#             result['errors'].append(f'unknown_page:{page_id}')
#             continue
#         PlatformConnection.objects.filter(pk=conn.pk).update(last_webhook_at=timezone.now())

#         for change in entry.get('changes', []):
#             if change.get('field') != 'leadgen':
#                 continue
#             value = change.get('value') or {}
#             leadgen_id = str(value.get('leadgen_id') or '')
#             if not leadgen_id:
#                 continue
#             try:
#                 event = record_event(value, page_id)
#                 if event is None:
#                     result['skipped'] += 1
#                     continue
#                 outcome = process_event(event.pk)
#             except Exception as exc:                      # never fail the whole POST
#                 logger.exception('leadgen %s failed', leadgen_id)
#                 result['errors'].append(f'{leadgen_id}: {mla.redact(exc)}')
#                 continue
#             if outcome == 'processed':
#                 result['accepted'] += 1
#             elif outcome == 'skipped':
#                 result['skipped'] += 1
#             else:
#                 result['queued'] += 1                     # will be retried automatically
#     return result


# def record_event(value, page_id):
#     """Durably store the notification. Returns the event to process, or None when this
#     leadgen_id is already done / already waiting for its retry slot."""
#     leadgen_id = str(value.get('leadgen_id'))
#     defaults = {
#         'page_id': str(value.get('page_id') or page_id or ''),
#         'form_id': str(value.get('form_id') or ''),
#         'ad_id': str(value.get('ad_id') or ''),
#         'adset_id': str(value.get('adgroup_id') or value.get('adset_id') or ''),
#         'campaign_id': str(value.get('campaign_id') or ''),
#         'raw_payload': value,
#     }
#     try:
#         with transaction.atomic():
#             event, created = MetaWebhookEvent.objects.get_or_create(leadgen_id=leadgen_id,
#                                                                     defaults=defaults)
#     except IntegrityError:                                # concurrent duplicate delivery
#         event, created = MetaWebhookEvent.objects.get(leadgen_id=leadgen_id), False
#     if created:
#         return event
#     if event.processed or event.gave_up:
#         return None
#     if event.next_attempt_at and event.next_attempt_at > timezone.now():
#         return None                                       # waiting for its back-off slot
#     return event


# # ---------------------------------------------------------------- process one

# def _fail(event, message, conn=None):
#     event.attempts += 1
#     event.error = mla.redact(message, conn.get_page_token() if conn else '')[:1000]
#     if event.attempts >= C.WEBHOOK_MAX_ATTEMPTS:
#         event.gave_up, event.next_attempt_at = True, None
#         logger.error('Meta leadgen %s gave up after %s attempts: %s',
#                      event.leadgen_id, event.attempts, event.error)
#         outcome = 'gave_up'
#     else:
#         delay = C.WEBHOOK_RETRY_BACKOFF[min(event.attempts - 1, len(C.WEBHOOK_RETRY_BACKOFF) - 1)]
#         event.next_attempt_at = timezone.now() + timedelta(seconds=delay)
#         outcome = 'retry'
#     event.save(update_fields=['attempts', 'error', 'gave_up', 'next_attempt_at'])
#     return outcome


# def process_event(event_id):
#     """Fetch the lead from Meta and upsert it. Returns processed / skipped / retry / gave_up.
#     Safe to call concurrently and repeatedly."""
#     event = MetaWebhookEvent.objects.filter(pk=event_id).first()
#     if event is None or event.processed:
#         return 'skipped'

#     conn = (PlatformConnection.objects.filter(platform=C.PLATFORM_META, page_id=event.page_id).first()
#             or PlatformConnection.objects.filter(platform=C.PLATFORM_META).first())
#     token = conn.get_page_token() if conn else ''
#     if not token:
#         return _fail(event, 'No Page Access Token configured.', conn)

#     ok, data = mla.fetch_lead(event.leadgen_id, token, api_version=conn.effective_api_version)
#     if not ok:
#         return _fail(event, f'Graph fetch failed: {data}', conn)

#     try:
#         with transaction.atomic():
#             locked = MetaWebhookEvent.objects.select_for_update().get(pk=event.pk)
#             if locked.processed:                          # another worker finished first
#                 return 'skipped'
#             fields = mla.parse_field_data(data.get('field_data', []))
#             lead, _created = upsert_lead_from_platform(
#                 C.PLATFORM_META,
#                 name=fields.get('name', ''), email=fields.get('email', ''),
#                 phone=fields.get('contact_number', ''), company=fields.get('company', ''),
#                 consent=C.CONSENT_GRANTED,   # submitting a Meta lead form = consent to be contacted
#                 external_lead_id=event.leadgen_id,
#                 page_id=event.page_id,
#                 form_id=str(data.get('form_id') or event.form_id or ''),
#                 campaign_id=str(data.get('campaign_id') or event.campaign_id or ''),
#                 campaign_name=str(data.get('campaign_name') or ''),
#                 adset_id=str(data.get('adset_id') or event.adset_id or ''),
#                 adset_name=str(data.get('adset_name') or ''),
#                 ad_id=str(data.get('ad_id') or event.ad_id or ''),
#                 ad_name=str(data.get('ad_name') or ''),
#                 form_answers=fields['_raw'],
#                 run_ai=True,
#             )
#             locked.campaign_id = str(data.get('campaign_id') or locked.campaign_id or '')
#             locked.adset_id = str(data.get('adset_id') or locked.adset_id or '')
#             locked.ad_id = str(data.get('ad_id') or locked.ad_id or '')
#             locked.form_id = str(data.get('form_id') or locked.form_id or '')
#             locked.lead_data = data
#             locked.lead = lead
#             locked.processed, locked.processed_at = True, timezone.now()
#             locked.error, locked.next_attempt_at = '', None
#             locked.save()
#     except Exception as exc:
#         logger.exception('Meta leadgen %s: CRM import failed', event.leadgen_id)
#         event.refresh_from_db()
#         return _fail(event, f'CRM import failed: {exc}', conn)
#     return 'processed'


# # ---------------------------------------------------------------- retry

# def retry_due(limit=50):
#     """Re-run events whose back-off has elapsed (and events orphaned by a crash)."""
#     now = timezone.now()
#     due = MetaWebhookEvent.objects.filter(processed=False, gave_up=False).filter(
#         Q(next_attempt_at__lte=now)
#         | Q(next_attempt_at__isnull=True, created_at__lte=now - timedelta(minutes=2))
#     ).order_by('id')[:limit]
#     counts = {'processed': 0, 'skipped': 0, 'retry': 0, 'gave_up': 0}
#     for ev in list(due):
#         try:
#             counts[process_event(ev.pk)] += 1
#         except Exception:
#             logger.exception('Meta leadgen retry crashed for event %s', ev.pk)
#             counts['retry'] += 1
#     return counts


# def reset_and_process(event):
#     """Manual retry from the admin UI: clears the retry state and runs it right away."""
#     if event.processed:
#         return 'skipped'
#     MetaWebhookEvent.objects.filter(pk=event.pk).update(
#         attempts=0, gave_up=False, next_attempt_at=None, error='')
#     return process_event(event.pk)









"""Meta Lead Ads webhook processing.

  signed POST -> store one MetaWebhookEvent per leadgen_id (UNIQUE) -> fetch the lead from
  the Graph API -> upsert the CRM lead.

Reliability model
  * The event row is committed BEFORE any Graph/CRM work, so a notification is never lost.
  * Replays are idempotent: UNIQUE(leadgen_id) on the event, a `processed` check under a row
    lock, and the touchpoint lookup in upsert_lead_from_platform.
  * Anything that fails (Graph down, token expired, CRM error) is retried with back-off by
    `retry_due()` (run by the process_conversion_queue command); after
    WEBHOOK_MAX_ATTEMPTS the event is marked gave_up and shows in the admin UI for a manual retry.
  * Meta always gets HTTP 200 for a correctly SIGNED request - retries are ours to manage.
"""
import json
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from capi import conf
from capi import constants as C
from capi.models import MetaWebhookEvent, PlatformConnection
from capi.services import meta_lead_ads as mla
from capi.services.intake import upsert_lead_from_platform

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- receive

def process_payload(raw_body, signature_header):
    """Returns {ok, accepted, skipped, queued, errors}. ok=False only for an unsigned /
    malformed request (the view answers 401); everything else is stored and retried."""
    result = {'ok': True, 'accepted': 0, 'skipped': 0, 'queued': 0, 'errors': []}

    if not mla.verify_signature(raw_body, signature_header, conf.get('META_APP_SECRET')):
        result.update(ok=False, errors=['signature_mismatch'])
        return result
    try:
        payload = json.loads(raw_body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        result.update(ok=False, errors=['invalid_json'])
        return result
    if payload.get('object') != 'page':
        result.update(ok=False, errors=['unexpected_object'])
        return result

    for entry in payload.get('entry', []):
        page_id = str(entry.get('id') or '')
        conn = PlatformConnection.objects.filter(platform=C.PLATFORM_META, page_id=page_id).first()
        if conn is None:
            result['errors'].append(f'unknown_page:{page_id}')
            continue
        PlatformConnection.objects.filter(pk=conn.pk).update(last_webhook_at=timezone.now())

        for change in entry.get('changes', []):
            if change.get('field') != 'leadgen':
                continue
            value = change.get('value') or {}
            leadgen_id = str(value.get('leadgen_id') or '')
            if not leadgen_id:
                continue
            try:
                event = record_event(value, page_id)
                if event is None:
                    result['skipped'] += 1
                    continue
                outcome = process_event(event.pk)
            except Exception as exc:                      # never fail the whole POST
                logger.exception('leadgen %s failed', leadgen_id)
                result['errors'].append(f'{leadgen_id}: {mla.redact(exc)}')
                continue
            if outcome == 'processed':
                result['accepted'] += 1
            elif outcome == 'skipped':
                result['skipped'] += 1
            else:
                result['queued'] += 1                     # will be retried automatically
    return result


def record_event(value, page_id):
    """Durably store the notification. Returns the event to process, or None when this
    leadgen_id is already done / already waiting for its retry slot."""
    leadgen_id = str(value.get('leadgen_id'))
    defaults = {
        'page_id': str(value.get('page_id') or page_id or ''),
        'form_id': str(value.get('form_id') or ''),
        'ad_id': str(value.get('ad_id') or ''),
        'adset_id': str(value.get('adgroup_id') or value.get('adset_id') or ''),
        'campaign_id': str(value.get('campaign_id') or ''),
        'raw_payload': value,
    }
    try:
        with transaction.atomic():
            event, created = MetaWebhookEvent.objects.get_or_create(leadgen_id=leadgen_id,
                                                                    defaults=defaults)
    except IntegrityError:                                # concurrent duplicate delivery
        event, created = MetaWebhookEvent.objects.get(leadgen_id=leadgen_id), False
    if created:
        return event
    if event.processed or event.gave_up:
        return None
    if event.next_attempt_at and event.next_attempt_at > timezone.now():
        return None                                       # waiting for its back-off slot
    return event


# ---------------------------------------------------------------- process one

def _fail(event, message, conn=None):
    event.attempts += 1
    event.error = mla.redact(message, conn.get_page_token() if conn else '')[:1000]
    if event.attempts >= C.WEBHOOK_MAX_ATTEMPTS:
        event.gave_up, event.next_attempt_at = True, None
        logger.error('Meta leadgen %s gave up after %s attempts: %s',
                     event.leadgen_id, event.attempts, event.error)
        outcome = 'gave_up'
    else:
        delay = C.WEBHOOK_RETRY_BACKOFF[min(event.attempts - 1, len(C.WEBHOOK_RETRY_BACKOFF) - 1)]
        event.next_attempt_at = timezone.now() + timedelta(seconds=delay)
        outcome = 'retry'
    event.save(update_fields=['attempts', 'error', 'gave_up', 'next_attempt_at'])
    return outcome


def process_event(event_id):
    """Fetch the lead from Meta and upsert it. Returns processed / skipped / retry / gave_up.
    Safe to call concurrently and repeatedly."""
    event = MetaWebhookEvent.objects.filter(pk=event_id).first()
    if event is None or event.processed:
        return 'skipped'

    conn = (PlatformConnection.objects.filter(platform=C.PLATFORM_META, page_id=event.page_id).first()
            or PlatformConnection.objects.filter(platform=C.PLATFORM_META).first())
    token = conn.get_page_token() if conn else ''
    if not token:
        return _fail(event, 'No Page Access Token configured.', conn)

    ok, data = mla.fetch_lead(event.leadgen_id, token, api_version=conn.effective_api_version)
    if not ok:
        return _fail(event, f'Graph fetch failed: {data}', conn)

    try:
        with transaction.atomic():
            locked = MetaWebhookEvent.objects.select_for_update().get(pk=event.pk)
            if locked.processed:                          # another worker finished first
                return 'skipped'
            fields = mla.parse_field_data(data.get('field_data', []))
            lead, _created = upsert_lead_from_platform(
                C.PLATFORM_META,
                name=fields.get('name', ''),
                email=fields.get('email', ''),
                phone=fields.get('contact_number', ''),
                company=fields.get('company', ''),
                job_title=fields.get('job_title', ''),          # <-- NAYA
                consent=C.CONSENT_GRANTED,   # submitting a Meta lead form = consent to be contacted
                external_lead_id=event.leadgen_id,
                page_id=event.page_id,
                form_id=str(data.get('form_id') or event.form_id or ''),
                campaign_id=str(data.get('campaign_id') or event.campaign_id or ''),
                campaign_name=str(data.get('campaign_name') or ''),
                adset_id=str(data.get('adset_id') or event.adset_id or ''),
                adset_name=str(data.get('adset_name') or ''),
                ad_id=str(data.get('ad_id') or event.ad_id or ''),
                ad_name=str(data.get('ad_name') or ''),
                form_answers=fields['_raw'],
                run_ai=True,
            )
            locked.campaign_id = str(data.get('campaign_id') or locked.campaign_id or '')
            locked.adset_id = str(data.get('adset_id') or locked.adset_id or '')
            locked.ad_id = str(data.get('ad_id') or locked.ad_id or '')
            locked.form_id = str(data.get('form_id') or locked.form_id or '')
            locked.lead_data = data
            locked.lead = lead
            locked.processed, locked.processed_at = True, timezone.now()
            locked.error, locked.next_attempt_at = '', None
            locked.save()
    except Exception as exc:
        logger.exception('Meta leadgen %s: CRM import failed', event.leadgen_id)
        event.refresh_from_db()
        return _fail(event, f'CRM import failed: {exc}', conn)
    return 'processed'


# ---------------------------------------------------------------- retry

def retry_due(limit=50):
    """Re-run events whose back-off has elapsed (and events orphaned by a crash)."""
    now = timezone.now()
    due = MetaWebhookEvent.objects.filter(processed=False, gave_up=False).filter(
        Q(next_attempt_at__lte=now)
        | Q(next_attempt_at__isnull=True, created_at__lte=now - timedelta(minutes=2))
    ).order_by('id')[:limit]
    counts = {'processed': 0, 'skipped': 0, 'retry': 0, 'gave_up': 0}
    for ev in list(due):
        try:
            counts[process_event(ev.pk)] += 1
        except Exception:
            logger.exception('Meta leadgen retry crashed for event %s', ev.pk)
            counts['retry'] += 1
    return counts


def reset_and_process(event):
    """Manual retry from the admin UI: clears the retry state and runs it right away."""
    if event.processed:
        return 'skipped'
    MetaWebhookEvent.objects.filter(pk=event.pk).update(
        attempts=0, gave_up=False, next_attempt_at=None, error='')
    return process_event(event.pk)