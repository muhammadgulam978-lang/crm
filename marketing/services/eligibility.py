"""Contact eligibility — do not send until this says yes."""
from django.utils import timezone

from marketing import constants as C
from marketing.models import MarketingConsent, SuppressionEntry


def normalize_value(channel, value):
    import re
    if channel == C.CHANNEL_EMAIL:
        return (value or '').strip().lower()
    return re.sub(r'\D', '', value or '')


def is_suppressed(channel, value):
    v = normalize_value(channel, value)
    if not v:
        return False
    now = timezone.now()
    qs = SuppressionEntry.objects.filter(channel=channel, value=v)
    return qs.filter(expires_at__isnull=True).exists() or qs.filter(expires_at__gt=now).exists()


def has_consent(lead, channel, purpose=C.PURPOSE_MARKETING):
    if not lead:
        return False
    return MarketingConsent.objects.filter(
        lead=lead, channel=channel, purpose=purpose,
        status=C.CONSENT_GRANTED,
    ).exists()


def can_contact(lead, channel, purpose=C.PURPOSE_MARKETING):
    """Return (ok: bool, reason: str)."""
    if not lead:
        return False, 'no lead'
    if channel == C.CHANNEL_EMAIL:
        value = lead.email or ''
    elif channel in (C.CHANNEL_WHATSAPP, C.CHANNEL_SMS, C.CHANNEL_CALL):
        value = lead.contact_number or ''
    else:
        value = ''
    if not value:
        return False, f'no {channel} value on lead'
    if is_suppressed(channel, value):
        return False, 'suppressed'
    if not has_consent(lead, channel, purpose):
        return False, 'no consent'
    return True, 'ok'


def eligibility_summary(lead):
    """For UI — all channels with reasons."""
    out = []
    for ch in (C.CHANNEL_EMAIL, C.CHANNEL_WHATSAPP, C.CHANNEL_CALL):
        ok, reason = can_contact(lead, ch)
        out.append({'channel': ch, 'ok': ok, 'reason': reason})
    return out