"""DB-driven automation runner — no Celery needed."""
import logging
from datetime import timedelta

from django.utils import timezone

from .. import constants as C
from ..models import AutomationRun, AutomationStep

logger = logging.getLogger(__name__)


def run_due(limit=50):
    """Process due AutomationRun rows. Returns counts dict."""
    counts = {'advanced': 0, 'done': 0, 'failed': 0}
    due = AutomationRun.objects.filter(
        status__in=[C.AUTOMATION_STATUS_CHOICES[0][0], 'running'],
        next_run_at__lte=timezone.now(),
    ).order_by('next_run_at')[:limit]

    for run in due:
        try:
            _advance(run, counts)
        except Exception as exc:
            logger.exception('automation run %s failed', run.pk)
            run.status = 'failed'
            run.last_error = str(exc)[:1000]
            run.save(update_fields=['status', 'last_error', 'updated_at'])
            counts['failed'] += 1
    return counts


def _advance(run, counts):
    step = run.steps.filter(order=run.current_step).first()
    if step is None:
        run.status = 'done'
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'finished_at', 'updated_at'])
        counts['done'] += 1
        return

    # 'wait' just moves time forward; other kinds would call into leads/chatbot here
    if step.kind == 'wait' and step.delay_seconds:
        run.next_run_at = timezone.now() + timedelta(seconds=step.delay_seconds)
    else:
        step.executed_at = timezone.now()
        step.success = True
        step.save(update_fields=['executed_at', 'success'])

    run.current_step += 1
    run.status = 'running'
    run.save(update_fields=['current_step', 'status', 'updated_at'])
    counts['advanced'] += 1