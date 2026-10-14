"""
Public website lead-form handling.

Design:
  * No authentication required — the endpoint is public.
  * Honeypot + rate limit guard against bots.
  * UTM params captured from the URL query string.
  * Reuses capi.services.intake.find_existing_lead for dedup so a website
    enquiry never creates a second CRM row for an existing contact.
  * Consent is recorded as GRANTED because the visitor explicitly submitted
    the form (source = web_form).
"""
import logging
import re
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from leads.models import CommunicationLog, Lead
from marketing import constants as C
from marketing.models import MarketingConsent, ProspectSource

logger = logging.getLogger(__name__)

# Rate limit: N submissions per hour per IP
RATE_LIMIT = 5
RATE_WINDOW_SECONDS = 3600

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
PHONE_RE = re.compile(r'[\d\s\+\-\(\)]{7,20}')

# Only these UTM keys are captured (ignore arbitrary query strings)
UTM_KEYS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content')


def normalize_phone(phone):
    return re.sub(r'\D', '', phone or '')


def normalize_email(email):
    return (email or '').strip().lower()


def rate_limited(ip):
    """Return True if the IP has exceeded the submission rate."""
    if not ip:
        return False
    key = f'marketing_lead_form_rl:{ip}'
    n = cache.get(key, 0)
    if n >= RATE_LIMIT:
        return True
    cache.set(key, n + 1, RATE_WINDOW_SECONDS)
    return False


def extract_utm(request):
    """Pull only known UTM keys from the query string. Always returns dict."""
    out = {}
    for key in UTM_KEYS:
        v = request.GET.get(key) or request.POST.get(key) or ''
        out[key] = (v or '').strip()[:200]
    return out


def _find_or_create_source(utm):
    """Return a ProspectSource for the UTM campaign, creating it if needed.
    Keeps the Marketing Center reporting clean without forcing admins to pre-create sources."""
    if not (utm.get('utm_source') or utm.get('utm_campaign')):
        # Fall back to a single generic 'Website form' source
        name = 'Website form'
        kind = C.SOURCE_KIND_INBOUND_FORM
    else:
        parts = [utm.get('utm_source'), utm.get('utm_campaign')]
        name = ' / '.join(p for p in parts if p)[:150] or 'Website form'
        kind = C.SOURCE_KIND_INBOUND_FORM

    source, _ = ProspectSource.objects.get_or_create(
        name=name, kind=kind,
        defaults={
            'is_active': True,
            'utm_source': utm.get('utm_source', ''),
            'utm_medium': utm.get('utm_medium', ''),
            'utm_campaign': utm.get('utm_campaign', ''),
        },
    )
    return source


def build_detail(data):
    """Human-readable summary for Lead.detail."""
    parts = []
    if data.get('company'):
        parts.append(f"Company: {data['company']}")
    if data.get('industry'):
        parts.append(f"Industry: {data['industry']}")
    if data.get('budget'):
        parts.append(f"Budget: {data['budget']}")
    if data.get('message'):
        parts.append(f"Message: {data['message']}")
    return '\n'.join(parts) or None


@transaction.atomic
def create_lead_from_form(*, request, cleaned, utm):
    """
    Create (or link to) a Lead from a validated public form submission.

    `cleaned` must contain: name, email, phone, company, industry, budget, message.
    Returns dict: {lead, created, source, matched_existing}
    """
    from capi.services.intake import find_existing_lead

    email = normalize_email(cleaned.get('email'))
    phone_digits = normalize_phone(cleaned.get('phone'))

    # Dedup via the CAPI intake helper (matches on email → phone tail)
    existing = find_existing_lead(
        platform=C.SOURCE_KIND_INBOUND_FORM,  # unused for matching, just a key
        email=email,
        phone=phone_digits,
        external_lead_id='',
    )

    source = _find_or_create_source(utm)
    created = False

    if existing is None:
        lead = Lead.objects.create(
            name=cleaned['name'],
            email=email or None,
            contact_number=cleaned.get('phone') or '',
            detail=build_detail(cleaned),
            status='new',
            lead_source='website',
            marketing_source=source,
            business_requirement=cleaned.get('message') or None,
            source_url=request.META.get('HTTP_REFERER', '')[:500] or None,
            research_date=timezone.localdate(),
        )
        created = True
    else:
        lead = existing
        # Enrich if fields are empty
        changed = []
        if not lead.email and email:
            lead.email = email
            changed.append('email')
        if not lead.contact_number and cleaned.get('phone'):
            lead.contact_number = cleaned['phone']
            changed.append('contact_number')
        if changed:
            lead.save(update_fields=changed)

    # ---- Consent: submitting the form = granted ----
    MarketingConsent.objects.update_or_create(
        lead=lead, channel=C.CHANNEL_EMAIL, purpose=C.PURPOSE_MARKETING,
        defaults={
            'status': C.CONSENT_GRANTED,
            'source': C.CONSENT_SOURCE_WEB_FORM,
            'source_ref': request.get_full_path()[:200],
            'captured_ip': _client_ip(request),
            'captured_user_agent': request.META.get('HTTP_USER_AGENT', '')[:500],
        },
    )
    if cleaned.get('phone'):
        MarketingConsent.objects.update_or_create(
            lead=lead, channel=C.CHANNEL_CALL, purpose=C.PURPOSE_MARKETING,
            defaults={
                'status': C.CONSENT_GRANTED,
                'source': C.CONSENT_SOURCE_WEB_FORM,
                'source_ref': request.get_full_path()[:200],
                'captured_ip': _client_ip(request),
                'captured_user_agent': request.META.get('HTTP_USER_AGENT', '')[:500],
            },
        )

    # ---- Timeline entry ----
    try:
        CommunicationLog.objects.create(
            comm_type='note',
            direction='inbound',
            subject='Website lead form submission',
            body=(
                f"Submitted from: {request.META.get('HTTP_REFERER', 'unknown')}\n"
                f"UTM: source={utm.get('utm_source') or '-'} "
                f"medium={utm.get('utm_medium') or '-'} "
                f"campaign={utm.get('utm_campaign') or '-'}\n\n"
                + (build_detail(cleaned) or '(no message)')
            ),
            lead=lead,
        )
    except Exception:
        logger.exception('CommunicationLog failed for website lead %s', lead.pk)

    return {'lead': lead, 'created': created, 'source': source, 'matched_existing': existing is not None}


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def is_honeypot_filled(post_data):
    """Any of these fields being filled = bot. Real users never see them."""
    traps = ('website_url', 'company_website', 'url_field', 'fax_number')
    return any((post_data.get(f) or '').strip() for f in traps)