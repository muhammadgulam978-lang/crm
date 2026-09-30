"""LinkedIn Conversions API (separate from Lead Sync - outcomes flow CRM -> LinkedIn)."""
import time
from urllib.parse import quote

import requests
from django.conf import settings

from . import identifiers as ids
from .base import NoIdentifiers, SendResult, short

TIMEOUT = 20
BASE = 'https://api.linkedin.com/rest'


def _headers(conn):
    return {
        'Authorization': f'Bearer {conn.get_token()}',
        'Content-Type': 'application/json',
        'LinkedIn-Version': conn.effective_api_version,
        'X-Restli-Protocol-Version': '2.0.0',
    }


def build_payload(event, lead, tracking, conn, cfg):
    user_ids, used = [], []
    em = ids.hashed_email(lead.email)
    if em:
        user_ids.append({'idType': 'SHA256_EMAIL', 'idValue': em}); used.append('SHA256_EMAIL')
    if tracking and tracking.li_fat_id:
        user_ids.append({'idType': 'LINKEDIN_FIRST_PARTY_ADS_TRACKING_UUID',
                         'idValue': tracking.li_fat_id}); used.append('li_fat_id')
    if not used:
        raise NoIdentifiers('LinkedIn needs a hashed email or li_fat_id.')

    user = {'userIds': user_ids}
    if getattr(settings, 'CAPI_LINKEDIN_SEND_USER_INFO', False):
        first, last = ids.split_name(lead.name)
        if first and last:  # LinkedIn requires both when userInfo is present
            user['userInfo'] = {'firstName': first, 'lastName': last}
            used.append('userInfo')

    payload = {
        'conversion': cfg.linkedin_conversion_urn,
        'conversionHappenedAt': int(event.event_time.timestamp() * 1000),  # milliseconds
        'eventId': event.event_id,
        'user': user,
    }
    if event.value is not None:
        payload['conversionValue'] = {'currencyCode': event.currency, 'amount': str(event.value)}
    return payload, used


def send(conn, payload):
    started = time.monotonic()
    try:
        r = requests.post(f'{BASE}/conversionEvents', json=payload, headers=_headers(conn), timeout=TIMEOUT)
    except requests.RequestException as exc:
        return SendResult(False, retryable=True, error_code='network', error_message=str(exc)[:500])
    ms = int((time.monotonic() - started) * 1000)
    text = short(r.text)
    trace = r.headers.get('x-li-uuid', '') or r.headers.get('x-linkedin-request-id', '')
    if 200 <= r.status_code < 300:
        return SendResult(True, r.status_code, text, trace_id=trace, events_received=1, duration_ms=ms)
    try:
        js = r.json()
    except ValueError:
        js = {}
    return SendResult(
        False, r.status_code, text,
        retryable=r.status_code >= 500 or r.status_code == 429,
        token_invalid=r.status_code in (401, 403),
        error_code=str(js.get('code') or r.status_code),
        error_message=js.get('message') or text[:500], trace_id=trace, duration_ms=ms)


def check_health(conn):
    """Lists conversion rules of the ad account - proves token + account access."""
    if not conn.ad_account_id:
        return False, 'Ad account ID is required for the health check.', False
    account = quote(f'urn:li:sponsoredAccount:{conn.ad_account_id}', safe='')
    try:
        r = requests.get(f'{BASE}/conversions?q=account&account={account}',
                         headers=_headers(conn), timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, f'Network error: {exc}', False
    if r.status_code == 200:
        n = len((r.json() or {}).get('elements', []))
        return True, f'Ad account reachable ({n} conversion rule(s) found).', False
    return False, f'HTTP {r.status_code}: {short(r.text, 300)}', r.status_code in (401, 403)


def associate_campaigns(conn, cfg):
    """Link the conversion rule to its sponsored campaigns (PUT /campaignConversions)."""
    conv = cfg.linkedin_conversion_urn
    results = []
    for campaign in [c.strip() for c in cfg.linkedin_campaign_urns.splitlines() if c.strip()]:
        key = f'(campaign:{quote(campaign, safe="")},conversion:{quote(conv, safe="")})'
        try:
            r = requests.put(f'{BASE}/campaignConversions/{key}', headers=_headers(conn),
                             json={'campaign': campaign, 'conversion': conv}, timeout=TIMEOUT)
            results.append((campaign, r.status_code in (200, 201, 204), r.status_code))
        except requests.RequestException as exc:
            results.append((campaign, False, str(exc)[:100]))
    return results
