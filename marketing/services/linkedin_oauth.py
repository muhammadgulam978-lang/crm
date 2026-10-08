"""LinkedIn OAuth 2.0 flow — mirror of the Google Calendar flow in leads."""
import requests
from urllib.parse import urlencode
from django.conf import settings


AUTH_URL = 'https://www.linkedin.com/oauth/v2/authorization'
TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
USERINFO_URL = 'https://api.linkedin.com/v2/userinfo'

# These are the scopes we need for organic posting (not ads).
# For ads / lead-sync, request 'r_ads', 'r_ads_reporting', 'rw_ads',
# 'r_organization_social' after LinkedIn approves your Marketing Developer Platform access.
DEFAULT_SCOPES = ['openid', 'profile', 'email', 'w_member_social']


def build_auth_url(redirect_uri, state, scopes=None):
    params = {
        'response_type': 'code',
        'client_id': settings.LINKEDIN_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'state': state,
        'scope': ' '.join(scopes or DEFAULT_SCOPES),
    }
    return f'{AUTH_URL}?{urlencode(params)}'


def exchange_code(code, redirect_uri):
    """Exchange auth code for access token."""
    r = requests.post(
        TOKEN_URL,
        data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': settings.LINKEDIN_CLIENT_ID,
            'client_secret': settings.LINKEDIN_CLIENT_SECRET,
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()   # {access_token, expires_in, refresh_token, refresh_token_expires_in, scope}


def refresh_token(refresh_token):
    r = requests.post(
        TOKEN_URL,
        data={
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': settings.LINKEDIN_CLIENT_ID,
            'client_secret': settings.LINKEDIN_CLIENT_SECRET,
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def fetch_userinfo(access_token):
    r = requests.get(USERINFO_URL, headers={'Authorization': f'Bearer {access_token}'}, timeout=20)
    r.raise_for_status()
    return r.json()   # {sub, name, email, picture, ...}