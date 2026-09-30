"""Event-trigger engine: CRM change -> business rules -> queued conversion event."""
import logging

from . import constants as C
from .models import ConversionEventConfig
from .services.queue import enqueue

logger = logging.getLogger(__name__)


def _matches(cfg, *, from_value, to_value, score, old_score):
    if cfg.trigger_key == C.TRIGGER_LEAD_SCORE:
        if not (old_score is not None and old_score < cfg.min_score <= (score or 0)):
            return False
    elif cfg.min_score is not None and (score or 0) < cfg.min_score:
        return False
    if cfg.from_value and cfg.from_value != (from_value or ''):
        return False
    if cfg.to_value and cfg.to_value != (to_value or ''):
        return False
    return True


def fire(trigger_key, lead, *, dedup_key, source_model='', source_id='',
         from_value='', to_value='', value=None, old_score=None):
    """Evaluate every ENABLED config for this trigger. Returns created events."""
    if lead is None:
        return []
    created = []
    configs = ConversionEventConfig.objects.filter(
        trigger_key=trigger_key, is_enabled=True, connection__is_active=True
    ).select_related('connection')
    for cfg in configs:
        if not _matches(cfg, from_value=from_value, to_value=to_value,
                        score=lead.ai_score, old_score=old_score):
            continue
        try:
            ev = enqueue(cfg, lead, trigger_key=trigger_key, dedup_key=dedup_key,
                         source_model=source_model, source_id=source_id, value=value)
        except Exception:
            logger.exception('CAPI enqueue failed (config %s, lead %s)', cfg.pk, lead.pk)
            continue
        if ev:
            created.append(ev)
    return created
