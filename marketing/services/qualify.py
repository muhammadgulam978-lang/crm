"""HOT / WARM / COLD qualification helpers.

Never modifies Lead.ai_score — that is owned by leads.ai_scorer.
This module only classifies and provides UI-friendly labels.

Usage:
    from marketing.services import qualify
    bucket = qualify.temperature(lead)         # -> 'hot' | 'warm' | 'cold' | 'unknown'
    info   = qualify.summary(lead)             # -> dict with bucket, score, reason
"""
from marketing import constants as C


def temperature_from_score(score):
    """Map an ai_score (0-100) to a HOT/WARM/COLD bucket. None/0-safe."""
    try:
        s = int(score or 0)
    except (TypeError, ValueError):
        return C.LEAD_TEMPERATURE_UNKNOWN
    if s >= C.HOT_THRESHOLD:
        return C.LEAD_TEMPERATURE_HOT
    if s >= C.WARM_THRESHOLD:
        return C.LEAD_TEMPERATURE_WARM
    if s > 0:
        return C.LEAD_TEMPERATURE_COLD
    return C.LEAD_TEMPERATURE_UNKNOWN


def temperature(lead):
    """Bucket for a Lead instance."""
    if lead is None:
        return C.LEAD_TEMPERATURE_UNKNOWN
    return temperature_from_score(getattr(lead, 'ai_score', 0))


def label(temperature_value):
    """Human-readable label for the bucket."""
    mapping = {
        C.LEAD_TEMPERATURE_HOT:     'HOT',
        C.LEAD_TEMPERATURE_WARM:    'WARM',
        C.LEAD_TEMPERATURE_COLD:    'COLD',
        C.LEAD_TEMPERATURE_UNKNOWN: 'Unknown',
    }
    return mapping.get(temperature_value, 'Unknown')


def badge_class(temperature_value):
    """CSS class for the UI badge."""
    mapping = {
        C.LEAD_TEMPERATURE_HOT:     'badge-red',
        C.LEAD_TEMPERATURE_WARM:    'badge-orange',
        C.LEAD_TEMPERATURE_COLD:    'badge-blue',
        C.LEAD_TEMPERATURE_UNKNOWN: 'badge-grey',
    }
    return mapping.get(temperature_value, 'badge-grey')


def summary(lead):
    """Full summary for a lead — bucket, score, thresholds, reason."""
    score = int(getattr(lead, 'ai_score', 0) or 0) if lead else 0
    bucket = temperature_from_score(score)
    reason = (
        getattr(lead, 'ai_score_reason', '') or
        getattr(lead, 'closing_probability_reason', '') or
        ''
    )
    return {
        'temperature': bucket,
        'label': label(bucket),
        'badge_class': badge_class(bucket),
        'score': score,
        'hot_threshold': C.HOT_THRESHOLD,
        'warm_threshold': C.WARM_THRESHOLD,
        'reason': reason,
    }


def bulk_temperatures(leads):
    """Return {lead_id: bucket} for a batch of leads (single query, no N+1)."""
    out = {}
    for lead in leads:
        out[lead.pk] = temperature_from_score(getattr(lead, 'ai_score', 0))
    return out