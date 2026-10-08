"""
Meta Lead Ads — webhook + Graph API integration.

Responsibilities:
  * verify the initial GET handshake (hub.mode / hub.verify_token / hub.challenge)
  * verify the X-Hub-Signature-256 header on every POST
  * fetch lead details from the Graph API using the page access token
  * parse the field_data array into our own schema
  * subscribe the page to the app so Meta starts sending leadgen events
"""
import hashlib
import hmac
import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_BASE = 'https://graph.facebook.com'
TIMEOUT = 20


# ---------------------------------------------------------------- handshake

def check_handshake(mode, token, challenge, expected_token):
    """Return challenge string if the handshake is valid, else None."""
    if mode != 'subscribe':
        return None
    if not expected_token:
        return None
    if not hmac.compare_digest(token or '', expected_token):
        return None
    return challenge or ''


# ---------------------------------------------------------------- signature

def verify_signature(raw_body, signature_header, app_secret):
    """X-Hub-Signature-256 = 'sha256=<hex>'. Constant-time compare on the hex."""
    if not app_secret:
        return False
    if not signature_header or not signature_header.startswith('sha256='):
        return False
    expected = hmac.new(
        app_secret.encode('utf-8'),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header.split('=', 1)[1]
    return hmac.compare_digest(expected, received)


# ---------------------------------------------------------------- graph fetch

def fetch_lead(leadgen_id, page_access_token, api_version='v21.0'):
    """GET /{leadgen_id}. Returns (ok, data_or_error)."""
    url = f'{GRAPH_BASE}/{api_version}/{leadgen_id}'
    try:
        r = requests.get(
            url,
            params={
                'access_token': page_access_token,
                'fields': 'id,created_time,field_data,ad_id,adset_id,campaign_id,form_id',
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.exception('Meta fetch_lead network error')
        return False, str(exc)

    try:
        data = r.json()
    except ValueError:
        return False, f'Non-JSON response (HTTP {r.status_code})'

    if r.status_code == 200 and 'error' not in data:
        return True, data
    err = data.get('error', {}) if isinstance(data, dict) else {}
    msg = err.get('message') or f'HTTP {r.status_code}'
    code = err.get('code')
    return False, f'{msg} (code={code})'


# ---------------------------------------------------------------- parsing

# Meta field_data uses `name` and `values`. We map to our own schema.
FIELD_ALIASES = {
    'full_name': 'name',
    'name': 'name',
    'first_name': 'first_name',
    'last_name': 'last_name',
    'email': 'email',
    'email_address': 'email',
    'phone_number': 'contact_number',
    'phone': 'contact_number',
    'mobile': 'contact_number',
    'company_name': 'company',
    'company': 'company',
    'job_title': 'job_title',
    'city': 'city',
    'country': 'country',
    'state': 'state',
    'zip_code': 'postcode',
    'post_code': 'postcode',
    'message': 'message',
    'what_are_you_looking_for': 'message',
}


def parse_field_data(field_data):
    """Turn Meta's field_data array into a flat dict with canonical keys.
    Keeps all original keys under `_raw` for safety."""
    out = {'_raw': {}}
    for item in field_data or []:
        key = (item.get('name') or '').strip().lower()
        values = item.get('values') or []
        value = (values[0] if values else '').strip() if values else ''
        if not key:
            continue
        out['_raw'][key] = value
        canonical = FIELD_ALIASES.get(key)
        if canonical and canonical not in out:
            out[canonical] = value
    # If first_name + last_name given but no full name, compose it
    if not out.get('name') and (out.get('first_name') or out.get('last_name')):
        out['name'] = f"{out.get('first_name', '')} {out.get('last_name', '')}".strip()
    return out


# ---------------------------------------------------------------- subscription

def subscribe_page(page_id, page_access_token, api_version='v21.0'):
    """POST /{page_id}/subscribed_apps — enables leadgen webhooks for this page."""
    url = f'{GRAPH_BASE}/{api_version}/{page_id}/subscribed_apps'
    try:
        r = requests.post(
            url,
            data={'subscribed_fields': 'leadgen', 'access_token': page_access_token},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, str(exc)
    try:
        data = r.json()
    except ValueError:
        return False, f'Non-JSON response (HTTP {r.status_code})'
    if r.status_code == 200 and data.get('success'):
        return True, 'subscribed'
    err = data.get('error', {}) if isinstance(data, dict) else {}
    return False, err.get('message') or f'HTTP {r.status_code}'


def list_subscriptions(page_id, page_access_token, api_version='v21.0'):
    """GET /{page_id}/subscribed_apps — check current subscription state."""
    url = f'{GRAPH_BASE}/{api_version}/{page_id}/subscribed_apps'
    try:
        r = requests.get(url, params={'access_token': page_access_token}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, str(exc)
    if r.status_code != 200:
        return False, f'HTTP {r.status_code}'
    return True, r.json().get('data', [])