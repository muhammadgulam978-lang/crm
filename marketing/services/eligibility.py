"""Contact eligibility — do not send until this says yes."""
from django.utils import timezone

from marketing import constants as C
from marketing.models import MarketingConsent, SuppressionEntry
from marketing.services.consent import (
    normalize_value, is_suppressed, has_consent,
)


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