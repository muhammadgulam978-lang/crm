"""Meta Conversions API (CRM / Conversion Leads style events)."""
import json
import time

import requests

from .. import constants as C
from . import identifiers as ids
from .base import NoIdentifiers, EventTooOld, SendResult, short

TIMEOUT = 20
RETRYABLE_CODES = {1, 2, 4, 17, 32, 341, 613}
TOKEN_CODES = {190, 102, 10} | set(range(200, 300))


def build_payload(event, lead, tracking, conn, cfg):
    """Returns (event_dict, identifiers_used). Sensitive data is limited to hashed
    email / phone / external_id; nothing else about the person is sent."""
    from django.utils import timezone
    from datetime import timedelta
    if event.event_time < timezone.now() - timedelta(days=C.META_MAX_EVENT_AGE_DAYS):
        raise EventTooOld('Meta only accepts events up to 7 days old.')

    user_data, used = {}, []
    em = ids.hashed_email(lead.email)
    if em:
        user_data['em'] = [em]; used.append('em')
    ph = ids.hashed_phone(lead.contact_number)
    if ph:
        user_data['ph'] = [ph]; used.append('ph')
    if tracking:
        lid = ''.join(ch for ch in (tracking.external_lead_id or '') if ch.isdigit())
        if tracking.platform == 'meta' and lid:
            user_data['lead_id'] = int(lid); used.append('lead_id')
        if tracking.fbc:
            user_data['fbc'] = tracking.fbc; used.append('fbc')
        if tracking.fbp:
            user_data['fbp'] = tracking.fbp; used.append('fbp')
        if tracking.client_ip and tracking.user_agent:
            user_data['client_ip_address'] = tracking.client_ip
            user_data['client_user_agent'] = tracking.user_agent
            used.append('ip_ua')
    if not used:
        raise NoIdentifiers('No email, phone, lead ID or click ID available for matching.')
    user_data['external_id'] = [ids.hashed_external_id(lead)]; used.append('external_id')

    custom = {'lead_event_source': conn.lead_event_source or 'SIS AI CRM', 'event_source': 'crm'}
    if event.campaign_id:
        custom['crm_campaign_id'] = event.campaign_id
    if event.campaign_name:
        custom['crm_campaign_name'] = event.campaign_name
    if event.value is not None:
        custom['value'] = float(event.value)
        custom['currency'] = event.currency

    payload = {
        'event_name': event.event_name,
        'event_time': int(event.event_time.timestamp()),
        'event_id': event.event_id,
        'action_source': 'system_generated',
        'user_data': user_data,
        'custom_data': custom,
    }
    return payload, used


def _error(js):
    err = (js or {}).get('error') or {}
    return err.get('code'), err.get('message', ''), err.get('fbtrace_id', '')


def send(conn, payload):
    url = f'https://graph.facebook.com/{conn.effective_api_version}/{conn.dataset_id}/events'
    body = {'data': [payload], 'access_token': conn.get_token()}
    if conn.test_event_code:
        body['test_event_code'] = conn.test_event_code
    started = time.monotonic()
    try:
        r = requests.post(url, json=body, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return SendResult(False, retryable=True, error_code='network', error_message=str(exc)[:500])
    ms = int((time.monotonic() - started) * 1000)
    try:
        js = r.json()
    except ValueError:
        js = {}
    text = short(r.text)

    if r.status_code == 200 and (js.get('events_received') or 0) >= 1:
        return SendResult(True, r.status_code, text, events_received=js.get('events_received'),
                          trace_id=js.get('fbtrace_id', ''), duration_ms=ms)
    if r.status_code == 200:
        return SendResult(False, r.status_code, text, error_code='not_received',
                          error_message='Meta returned 200 but events_received was 0.',
                          trace_id=js.get('fbtrace_id', ''), duration_ms=ms)

    code, msg, trace = _error(js)
    return SendResult(
        False, r.status_code, text,
        retryable=r.status_code >= 500 or r.status_code == 429 or code in RETRYABLE_CODES,
        token_invalid=code in TOKEN_CODES or r.status_code in (401, 403),
        error_code=str(code or r.status_code), error_message=msg or text[:500],
        trace_id=trace, duration_ms=ms)


def check_health(conn):
    """Read the dataset node - validates token + dataset id without sending events."""
    url = f'https://graph.facebook.com/{conn.effective_api_version}/{conn.dataset_id}'
    try:
        r = requests.get(url, params={'fields': 'id,name', 'access_token': conn.get_token()},
                         timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, f'Network error: {exc}', False
    if r.status_code == 200:
        name = (r.json() or {}).get('name', '')
        return True, f'Dataset reachable{" (" + name + ")" if name else ""}.', False
    code, msg, _ = _error(r.json() if r.content else {})
    return False, msg or f'HTTP {r.status_code}', code in TOKEN_CODES or r.status_code in (401, 403)
