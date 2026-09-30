"""Conversion queue: eligibility -> queue -> identifier preparation -> platform API -> response."""
import hashlib
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .. import constants as C
from ..models import (ConversionEvent, ConversionEventConfig, ConversionEventLog,
                      LeadTracking)
from . import linkedin, meta
from .base import EventTooOld, NoIdentifiers, short

logger = logging.getLogger(__name__)
SENDERS = {C.PLATFORM_META: meta, C.PLATFORM_LINKEDIN: linkedin}


def make_event_id(config, dedup_key):
    """Deterministic CRM event ID - also the platform-side dedup key."""
    return hashlib.sha256(f'sis-crm:{config.pk}:{dedup_key}'.encode()).hexdigest()[:40]


def is_eligible(config, lead, tracking):
    """Business-rule gate. Returns (bool, reason). Ineligible events are never queued."""
    conn = config.connection
    if not config.is_enabled:
        return False, 'event disabled'
    if not conn.is_active:
        return False, 'connection inactive'
    if lead.is_duplicate:
        return False, 'lead is a duplicate'
    if config.require_platform_match and (not tracking or tracking.platform != conn.platform):
        return False, 'lead not attributed to this platform'
    if config.require_consent:
        if not tracking or tracking.consent_status != C.CONSENT_GRANTED:
            return False, 'no consent'
    return True, ''


def enqueue(config, lead, *, trigger_key, dedup_key, source_model='', source_id='', value=None):
    """Create a queued ConversionEvent if eligible. Returns the event or None."""
    tracking = LeadTracking.objects.filter(lead=lead).first()
    ok, reason = is_eligible(config, lead, tracking)
    if not ok:
        logger.debug('CAPI skip lead=%s config=%s: %s', lead.pk, config.pk, reason)
        return None

    if config.fixed_value is not None:
        value = config.fixed_value
    if not config.send_value:
        value = None

    event = ConversionEvent(
        config=config, platform=config.connection.platform, lead=lead, lead_ref=lead.pk,
        lead_label=(lead.name or '')[:150], trigger_key=trigger_key,
        source_model=source_model, source_id=str(source_id), dedup_key=dedup_key,
        event_id=make_event_id(config, dedup_key),
        event_name=config.platform_event_name or config.name,
        event_time=timezone.now(), value=value,
        currency=config.effective_currency if value is not None else '',
        campaign_id=getattr(tracking, 'campaign_id', ''), campaign_name=getattr(tracking, 'campaign_name', ''),
        adset_id=getattr(tracking, 'adset_id', ''), ad_id=getattr(tracking, 'ad_id', ''),
    )
    try:
        with transaction.atomic():
            event.save()
    except IntegrityError:
        return None  # already queued/sent for this lead + event (idempotent)
    return event


# ---------------------------------------------------------------- delivery

def _claim(event):
    """Atomically move pending/retrying -> processing so two workers never send the same event."""
    n = ConversionEvent.objects.filter(
        pk=event.pk, status__in=[C.ST_PENDING, C.ST_RETRYING]
    ).update(status=C.ST_PROCESSING, last_attempt_at=timezone.now())
    return bool(n)


def deliver(event):
    """Send one claimed event and record the outcome."""
    event.refresh_from_db()
    cfg, conn, lead = event.config, event.config.connection, event.lead
    sender = SENDERS[event.platform]
    event.attempts += 1

    if lead is None:
        return _finish(event, C.ST_FAILED, error_code='lead_deleted', error_message='Lead no longer exists.')
    if not conn.is_ready:
        return _schedule_retry(event, 'connection_not_ready',
                               f'Connection not ready (token: {conn.token_status}).')

    tracking = LeadTracking.objects.filter(lead=lead).first()
    try:
        payload, used = sender.build_payload(event, lead, tracking, conn, cfg)
    except NoIdentifiers as exc:
        return _finish(event, C.ST_SKIPPED, error_code='no_identifiers', error_message=str(exc))
    except EventTooOld as exc:
        return _finish(event, C.ST_FAILED, error_code='event_too_old', error_message=str(exc))

    event.request_payload = payload
    event.identifiers_used = used
    result = sender.send(conn, payload)

    ConversionEventLog.objects.create(
        event=event, attempt=event.attempts, success=result.ok, status_code=result.status_code,
        message=result.error_message or 'OK', response_excerpt=short(result.body, 1500),
        duration_ms=result.duration_ms)

    event.response_status = result.status_code
    event.response_body = short(result.body)
    event.platform_trace_id = result.trace_id
    event.events_received = result.events_received

    if result.token_invalid:
        conn.token_invalid = True
        conn.health_status = 'error'
        conn.health_message = result.error_message[:500]
        conn.save(update_fields=['token_invalid', 'health_status', 'health_message'])

    if result.ok:
        event.sent_at = timezone.now()
        return _finish(event, C.ST_SENT)
    if result.retryable and event.attempts < C.MAX_ATTEMPTS:
        return _schedule_retry(event, result.error_code, result.error_message)
    return _finish(event, C.ST_FAILED, error_code=result.error_code, error_message=result.error_message)


def _finish(event, status, error_code='', error_message=''):
    event.status = status
    event.error_code = error_code
    event.error_message = error_message
    event.save()
    return event


def _schedule_retry(event, code, message):
    delay = C.RETRY_BACKOFF[min(event.attempts - 1, len(C.RETRY_BACKOFF) - 1)]
    event.next_attempt_at = timezone.now() + timedelta(seconds=delay)
    if event.attempts >= C.MAX_ATTEMPTS:
        return _finish(event, C.ST_FAILED, code, message)
    return _finish(event, C.ST_RETRYING, code, message)


def process_queue(limit=50):
    """Send due events. Run from the management command / scheduler. Returns counts."""
    # recover events stuck in 'processing' (worker crashed)
    ConversionEvent.objects.filter(
        status=C.ST_PROCESSING, last_attempt_at__lt=timezone.now() - timedelta(minutes=10)
    ).update(status=C.ST_RETRYING)

    due = ConversionEvent.objects.filter(
        status__in=[C.ST_PENDING, C.ST_RETRYING], next_attempt_at__lte=timezone.now()
    ).order_by('id')[:limit]
    counts = {'sent': 0, 'retrying': 0, 'failed': 0, 'skipped': 0}
    for ev in list(due):
        if not _claim(ev):
            continue
        try:
            done = deliver(ev)
        except Exception as exc:  # never let one event stop the batch
            logger.exception('CAPI deliver crashed for event %s', ev.pk)
            ev.refresh_from_db()
            done = _schedule_retry(ev, 'internal_error', str(exc)[:500])
        counts[done.status] = counts.get(done.status, 0) + 1
    return counts


def retry_now(event):
    """Manual retry (failed / retrying / skipped) - resets attempts and sends immediately."""
    if event.status in (C.ST_SENT, C.ST_PROCESSING):
        return event
    ConversionEvent.objects.filter(pk=event.pk).update(
        status=C.ST_PENDING, attempts=0, next_attempt_at=timezone.now(), error_code='', error_message='')
    event.refresh_from_db()
    if _claim(event):
        return deliver(event)
    return event
