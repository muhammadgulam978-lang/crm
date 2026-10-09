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
import logging
import re

import requests

from capi import conf

logger = logging.getLogger(__name__)

GRAPH_BASE = 'https://graph.facebook.com'
TIMEOUT = 20
DEFAULT_VERSION = 'v25.0'

LEAD_FIELDS_BASE = 'id,created_time,field_data,ad_id,adset_id,campaign_id,form_id'
LEAD_FIELDS_EXTENDED = LEAD_FIELDS_BASE + ',ad_name,adset_name,campaign_name'


# ---------------------------------------------------------------- secrets hygiene

_TOKEN_RE = re.compile(r'(access_token|appsecret_proof|client_secret)=[^&\s\'"]+', re.I)


def redact(text, *secrets):
    """Remove tokens from anything that may be stored or logged (requests puts the
    full URL, query string included, into its exception messages)."""
    text = _TOKEN_RE.sub(r'\1=[redacted]', str(text or ''))
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[redacted]')
    return text


def appsecret_proof(access_token):
    """Graph 'appsecret_proof' = HMAC-SHA256(access_token, app_secret). Proves the call
    comes from our server; required if 'Require App Secret' is on for the Meta app."""
    secret = conf.get('META_APP_SECRET')
    if not (secret and access_token):
        return {}
    return {'appsecret_proof': hmac.new(secret.encode(), access_token.encode(),
                                        hashlib.sha256).hexdigest()}


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

def _graph_error(r, token):
    try:
        data = r.json()
    except ValueError:
        return None, f'Non-JSON response (HTTP {r.status_code})'
    return data, None


def _fetch(leadgen_id, page_access_token, api_version, fields):
    url = f'{GRAPH_BASE}/{api_version}/{leadgen_id}'
    params = {'access_token': page_access_token, 'fields': fields,
              **appsecret_proof(page_access_token)}
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, redact(exc, page_access_token), None
    data, err = _graph_error(r, page_access_token)
    if err:
        return False, err, None
    if r.status_code == 200 and 'error' not in data:
        return True, data, None
    e = data.get('error', {}) if isinstance(data, dict) else {}
    code = e.get('code')
    return False, redact(f"{e.get('message') or f'HTTP {r.status_code}'} (code={code})",
                         page_access_token), code


def fetch_lead(leadgen_id, page_access_token, api_version=DEFAULT_VERSION):
    """GET /{leadgen_id}. Returns (ok, data_or_error_text).
    Asks for the campaign / ad set / ad NAMES too; if Graph rejects an optional field
    (error 100) it transparently falls back to the base field list."""
    ok, data, code = _fetch(leadgen_id, page_access_token, api_version, LEAD_FIELDS_EXTENDED)
    if not ok and code == 100:
        ok, data, code = _fetch(leadgen_id, page_access_token, api_version, LEAD_FIELDS_BASE)
    return ok, data


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
        value = (values[0] if values else '')
        value = (value or '').strip()
        if not key:
            continue
        out['_raw'][key] = value
        if len(values) > 1:
            out['_raw'][key] = ', '.join(v.strip() for v in values if v)
        canonical = FIELD_ALIASES.get(key)
        if canonical == 'contact_number':
            value = re.sub(r'^p:\s*', '', value)      # Meta exports prefix phones with "p:"
        if canonical and not out.get(canonical):
            out[canonical] = value
    # If first_name + last_name given but no full name, compose it
    if not out.get('name') and (out.get('first_name') or out.get('last_name')):
        out['name'] = f"{out.get('first_name', '')} {out.get('last_name', '')}".strip()
    return out


# ---------------------------------------------------------------- subscription

def subscribe_page(page_id, page_access_token, api_version=DEFAULT_VERSION):
    """POST /{page_id}/subscribed_apps — enables leadgen webhooks for this page."""
    url = f'{GRAPH_BASE}/{api_version}/{page_id}/subscribed_apps'
    try:
        r = requests.post(
            url,
            data={'subscribed_fields': 'leadgen', 'access_token': page_access_token,
                  **appsecret_proof(page_access_token)},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, redact(exc, page_access_token)
    try:
        data = r.json()
    except ValueError:
        return False, f'Non-JSON response (HTTP {r.status_code})'
    if r.status_code == 200 and data.get('success'):
        return True, 'subscribed'
    err = data.get('error', {}) if isinstance(data, dict) else {}
    return False, err.get('message') or f'HTTP {r.status_code}'


def list_subscriptions(page_id, page_access_token, api_version=DEFAULT_VERSION):
    """GET /{page_id}/subscribed_apps — check current subscription state."""
    url = f'{GRAPH_BASE}/{api_version}/{page_id}/subscribed_apps'
    try:
        r = requests.get(url, params={'access_token': page_access_token,
                                      **appsecret_proof(page_access_token)}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, redact(exc, page_access_token)
    try:
        data = r.json()
    except ValueError:
        return False, f'Non-JSON response (HTTP {r.status_code})'
    if r.status_code != 200:
        return False, redact((data.get('error') or {}).get('message') or f'HTTP {r.status_code}',
                             page_access_token)
    return True, data.get('data', [])


def page_has_leadgen_subscription(subscriptions):
    return any('leadgen' in (item.get('subscribed_fields') or []) for item in subscriptions or [])


def check_page(page_id, page_access_token, api_version=DEFAULT_VERSION):
    """GET /{page_id}?fields=id,name - validates the Page token. Returns (ok, message)."""
    try:
        r = requests.get(f'{GRAPH_BASE}/{api_version}/{page_id}',
                         params={'fields': 'id,name', 'access_token': page_access_token,
                                 **appsecret_proof(page_access_token)}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, redact(exc, page_access_token)
    try:
        data = r.json()
    except ValueError:
        return False, f'Non-JSON response (HTTP {r.status_code})'
    if r.status_code == 200:
        return True, f"Page reachable ({data.get('name', page_id)})."
    return False, redact((data.get('error') or {}).get('message') or f'HTTP {r.status_code}',
                         page_access_token)