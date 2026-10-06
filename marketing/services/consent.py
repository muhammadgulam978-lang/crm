"""Consent + suppression gating helpers."""
import re
from django.utils import timezone

from .. import constants as C
from ..models import MarketingConsent, SuppressionEntry


def normalize_email(email):
    return (email or '').strip().lower()


def normalize_phone(phone):
    return re.sub(r'\D', '', phone or '')


def normalize_value(channel, value):
    if channel == C.CHANNEL_EMAIL:
        return normalize_email(value)
    return normalize_phone(value)


def is_suppressed(channel, value):
    value = normalize_value(channel, value)
    if not value:
        return False
    now = timezone.now()
    qs = SuppressionEntry.objects.filter(channel=channel, value=value)
    return qs.filter(expires_at__isnull=True).exists() or \
           qs.filter(expires_at__gt=now).exists()


def has_consent(lead, channel, purpose=C.PURPOSE_MARKETING):
    if lead is None:
        return False
    return MarketingConsent.objects.filter(
        lead=lead, channel=channel, purpose=purpose,
        status=C.CONSENT_GRANTED,
    ).exists()


def can_contact(lead, channel, value, purpose=C.PURPOSE_MARKETING):
    """Combined gate: consent AND not suppressed. Returns (bool, reason)."""
    if lead is None:
        return False, 'no lead'
    if is_suppressed(channel, value):
        return False, 'suppressed'
    if not has_consent(lead, channel, purpose):
        return False, 'no consent'
    return True, ''