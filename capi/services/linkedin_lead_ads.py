"""
LinkedIn Lead Sync API — inbound webhook + REST integration.

Responsibilities:
  * verify the LinkedIn webhook challenge (challengeCode -> HMAC challengeResponse)
  * fetch lead details via LinkedIn REST (leadForms / responses)
  * parse the answers[] array into our canonical schema
  * health-check the org's lead notifications
"""
import hashlib
import hmac
import logging
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

REST_BASE = 'https://api.linkedin.com/rest'
TIMEOUT = 20


def build_challenge_response(challenge_code, client_secret):
    """HMAC-SHA256 hex of challengeCode, keyed by the app client secret."""
    return hmac.new(
        (client_secret or '').encode('utf-8'),
        (challenge_code or '').encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def verify_webhook_payload(raw_body, signature_header, client_secret):
    """Validate X-LI-Signature when LinkedIn sends one."""
    if not signature_header:
        return True
    received = signature_header.split('=', 1)[-1].strip()
    expected = hmac.new(
        (client_secret or '').encode('utf-8'), raw_body, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, received)


def _headers(conn):
    return {
        'Authorization': f'Bearer {conn.get_token()}',
        'LinkedIn-Version': conn.effective_api_version,
        'X-Restli-Protocol-Version': '2.0.0',
    }


def fetch_lead(conn, form_urn, response_urn):
    """GET /rest/leadForms/{formUrn}/responses/{responseUrn} -> (ok, data|error)."""
    url = (f'{REST_BASE}/leadForms/{quote(form_urn, safe="")}'
           f'/responses/{quote(response_urn, safe="")}')
    try:
        r = requests.get(url, headers=_headers(conn), timeout=TIMEOUT)
    except requests.RequestException as exc:
        logger.exception('LinkedIn fetch_lead network error')
        return False, str(exc)
    try:
        data = r.json()
    except ValueError:
        return False, f'Non-JSON response (HTTP {r.status_code})'
    if r.status_code == 200:
        return True, data
    err = (data or {}).get('message') or (data or {}).get('error') or f'HTTP {r.status_code}'
    return False, str(err)[:500]


FIELD_ALIASES = {
    'firstname': 'first_name', 'first_name': 'first_name',
    'lastname': 'last_name',  'last_name': 'last_name',
    'email': 'email', 'emailaddress': 'email', 'email_address': 'email', 'workemail': 'email',
    'phone': 'contact_number', 'phonenumber': 'contact_number',
    'phone_number': 'contact_number', 'mobilephone': 'contact_number',
    'companyname': 'company', 'company_name': 'company', 'company': 'company',
    'jobtitle': 'job_title', 'job_title': 'job_title', 'title': 'job_title',
    'city': 'city', 'country': 'country', 'state': 'state',
    'postalcode': 'postcode', 'postal_code': 'postcode', 'zip': 'postcode',
}


def parse_form_response(data):
    """Turn LinkedIn's answers[] into flat canonical fields."""
    out = {'_raw': {}}
    answers = (data.get('answers') or data.get('formResponse')
               or data.get('leadFormResponse') or [])
    for item in answers:
        if not isinstance(item, dict):
            continue
        q = (item.get('question') or item.get('questionUrn') or '').strip().lower()
        a = item.get('answer')
        if a is None:
            vals = item.get('answers') or item.get('values') or []
            a = ', '.join(str(v) for v in vals if v)
        a = (a or '').strip() if isinstance(a, str) else str(a or '')
        if not q:
            continue
        out['_raw'][q] = a
        canonical = FIELD_ALIASES.get(q) or FIELD_ALIASES.get(q.replace(' ', ''))
        if canonical and canonical not in out:
            out[canonical] = a

    if not out.get('name'):
        if out.get('first_name') or out.get('last_name'):
            out['name'] = f"{out.get('first_name', '')} {out.get('last_name', '')}".strip()
        elif out.get('email'):
            out['name'] = out['email'].split('@', 1)[0]
    return out


def check_health(conn):
    """List the org's lead notifications — proves token + org access."""
    if not conn.linkedin_organization_urn:
        return False, 'LinkedIn Organization URN is required.', False
    owner = quote(conn.linkedin_organization_urn, safe='')
    try:
        r = requests.get(f'{REST_BASE}/leadNotifications',
                         params={'q': 'owner', 'owner': owner},
                         headers=_headers(conn), timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, f'Network error: {exc}', False
    if r.status_code == 200:
        n = len((r.json() or {}).get('elements', []))
        return True, f'Organization reachable ({n} lead notification(s) found).', False
    return False, f'HTTP {r.status_code}: {r.text[:300]}', r.status_code in (401, 403)