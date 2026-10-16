"""DB-driven automation runner — executes each step, no Celery needed."""
import logging
from datetime import timedelta

from django.utils import timezone

from leads.models import FollowUp, GeneralTask
from marketing import constants as C
from marketing.models import AutomationRun, AutomationStep, AutomationRunLog, LeadStage
from marketing.services import drafting, reply_handler as rh

logger = logging.getLogger(__name__)


def run_due(limit=50):
    """
    Process due AutomationRun rows. Returns counts dict.
    Idempotent: uses AutomationRunLog to prevent duplicates.
    """
    counts = {'advanced': 0, 'done': 0, 'failed': 0, 'skipped': 0}
    due = AutomationRun.objects.filter(
        status__in=[
            C.AUTOMATION_STATUS_PENDING,
            C.AUTOMATION_STATUS_RUNNING,
        ],
        next_run_at__lte=timezone.now(),
    ).order_by('next_run_at')[:limit]

    for run in due:
        try:
            _advance(run, counts)
        except Exception as exc:
            logger.exception('automation run %s failed', run.pk)
            run.status = C.AUTOMATION_STATUS_FAILED
            run.last_error = str(exc)[:1000]
            run.save(update_fields=['status', 'last_error', 'updated_at'])
            counts['failed'] += 1
    return counts


def _advance(run, counts):
    """Execute the current step, then move to next."""
    run.refresh_from_db()
    if run.status in (C.AUTOMATION_STATUS_CANCELLED, C.AUTOMATION_STATUS_DONE):
        counts['skipped'] += 1
        return

    step = run.steps.filter(order=run.current_step).first()
    if step is None:
        # All steps done
        run.status = C.AUTOMATION_STATUS_DONE
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'finished_at', 'updated_at'])
        counts['done'] += 1
        return

    # --- Handle by kind ---
    if step.kind == C.STEP_KIND_WAIT:
        # Just advance time
        if step.delay_seconds:
            run.next_run_at = timezone.now() + timedelta(seconds=step.delay_seconds)
        step.executed_at = timezone.now()
        step.success = True
        step.save(update_fields=['executed_at', 'success'])

    elif step.kind == C.STEP_KIND_SEND_EMAIL:
        _execute_send_email(run, step)

    elif step.kind == C.STEP_KIND_SEND_WHATSAPP:
        _execute_send_whatsapp(run, step)

    elif step.kind == C.STEP_KIND_SEND_SMS:
        _execute_send_sms(run, step)

    elif step.kind == C.STEP_KIND_CREATE_TASK:
        _execute_create_task(run, step)

    elif step.kind == C.STEP_KIND_CREATE_FOLLOWUP:
        _execute_create_followup(run, step)

    elif step.kind == C.STEP_KIND_NOTIFY:
        _execute_notify(run, step)

    else:
        logger.warning('Unknown step kind: %s', step.kind)
        step.success = False
        step.result = {'error': f'Unknown kind: {step.kind}'}
        step.executed_at = timezone.now()
        step.save()

    # Advance pointer
    run.current_step += 1
    run.status = C.AUTOMATION_STATUS_RUNNING
    if not run.next_run_at or run.next_run_at <= timezone.now():
        # If no wait step set a new time, run next step immediately
        pass
    run.save(update_fields=['current_step', 'status', 'next_run_at', 'updated_at'])
    counts['advanced'] += 1


def _dedup_key(run, step):
    return f'run{run.pk}-step{step.order}-{step.kind}'


def _already_executed(run, step):
    """Check idempotency log."""
    return AutomationRunLog.objects.filter(
        lead=run.lead, dedup_key=_dedup_key(run, step),
    ).exists()


def _log_execution(run, step, payload=None):
    AutomationRunLog.objects.get_or_create(
        lead=run.lead,
        dedup_key=_dedup_key(run, step),
        defaults={'run': run, 'step_kind': step.kind, 'payload': payload or {}},
    )


# ---------------------------------------------------------------- Executors

def _execute_send_email(run, step):
    """
    Draft an email. If require_approval=True, save as draft and notify SPO.
    If require_approval=False, send immediately (only if eligible).
    """
    if _already_executed(run, step):
        step.success = True
        step.result = {'skipped': 'already_executed'}
        step.executed_at = timezone.now()
        step.save()
        return

    from marketing.services import eligibility as elig

    lead = run.lead
    template_kind = step.params.get('template_kind', 'follow_up')
    require_approval = step.params.get('require_approval', True)

    # Draft the message
    draft = drafting.draft(lead, template_kind)

    # Notify owner (draft ready for review)
    rh.notify_owner(
        lead,
        title=f'Draft ready: {lead.name}',
        message=(
            f'A follow-up email draft is ready for {lead.name}.\n\n'
            f'Subject: {draft["subject"]}\n\n'
            f'{draft["body"][:500]}'
        ),
        link=f'/leads/{lead.pk}/',
    )

    step.success = True
    step.result = {
        'draft_subject': draft['subject'],
        'draft_body_preview': draft['body'][:200],
        'source': draft['source'],
        'require_approval': require_approval,
    }
    step.executed_at = timezone.now()
    step.save()
    _log_execution(run, step, payload=step.result)


def _execute_send_whatsapp(run, step):
    """Same as email — draft + notify (external sending requires approval)."""
    _execute_send_email(run, step)  # reuse same logic for now


def _execute_send_sms(run, step):
    _execute_send_email(run, step)


def _execute_create_task(run, step):
    """Create a GeneralTask for the assigned SPO."""
    if _already_executed(run, step):
        step.success = True
        step.result = {'skipped': 'already_executed'}
        step.executed_at = timezone.now()
        step.save()
        return

    lead = run.lead
    user = lead.spo.user if lead.spo and lead.spo.user else None
    if not user:
        step.success = False
        step.result = {'error': 'no assigned user'}
        step.executed_at = timezone.now()
        step.save()
        return

    title = step.params.get('title', f'Follow up: {lead.name}')
    priority = step.params.get('priority', 'medium')

    task = GeneralTask.objects.create(
        title=title[:200],
        description=f'Auto-generated from automation run #{run.pk}',
        assigned_to=user,
        created_by=None,
        status='todo',
        priority=priority,
        lead=lead,
    )

    step.success = True
    step.result = {'task_id': task.pk, 'assigned_to': user.username}
    step.executed_at = timezone.now()
    step.save()
    _log_execution(run, step, payload=step.result)


def _execute_create_followup(run, step):
    """Create a FollowUp row (reminder)."""
    if _already_executed(run, step):
        step.success = True
        step.result = {'skipped': 'already_executed'}
        step.executed_at = timezone.now()
        step.save()
        return

    lead = run.lead
    days = int(step.params.get('days', 3))
    title = step.params.get('title', 'Auto follow-up')

    followup = FollowUp.objects.create(
        lead=lead,
        follow_up_date=timezone.now() + timedelta(days=days),
        status='pending',
        notes=title,
    )

    step.success = True
    step.result = {'followup_id': followup.pk, 'due_at': followup.follow_up_date.isoformat()}
    step.executed_at = timezone.now()
    step.save()
    _log_execution(run, step, payload=step.result)


def _execute_notify(run, step):
    """Send a notification to the SPO."""
    if _already_executed(run, step):
        step.success = True
        step.result = {'skipped': 'already_executed'}
        step.executed_at = timezone.now()
        step.save()
        return

    lead = run.lead
    title = step.params.get('title', f'Automation update: {lead.name}')
    message = step.params.get('message', 'Automation step executed.')

    notif = rh.notify_owner(lead, title=title, message=message, link=f'/leads/{lead.pk}/')

    step.success = True
    step.result = {'notification_id': notif.pk if notif else None}
    step.executed_at = timezone.now()
    step.save()
    _log_execution(run, step, payload=step.result)