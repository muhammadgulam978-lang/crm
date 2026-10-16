"""
Follow-up automation rules.

Each rule returns a list of actions to schedule for a lead.
Actions are executed by the automation runner (Phase 7).
"""
import logging
from datetime import timedelta

from django.utils import timezone

from leads.models import Lead
from marketing import constants as C
from marketing.models import AutomationRun, AutomationStep, LeadStage

logger = logging.getLogger(__name__)


def create_run(*, lead, name, steps, campaign=None, payload=None):
    """
    Create an AutomationRun with steps. Idempotent per (lead, name).
    Returns (run, created).
    """
    if not steps:
        return None, False

    # Idempotency: same lead + name = same run
    existing = AutomationRun.objects.filter(
        lead=lead,
        name=name,
        status__in=[
            C.AUTOMATION_STATUS_PENDING,
            C.AUTOMATION_STATUS_RUNNING,
        ],
    ).first()
    if existing:
        return existing, False

    run = AutomationRun.objects.create(
        lead=lead,
        name=name,
        campaign=campaign,
        status=C.AUTOMATION_STATUS_PENDING,
        current_step=0,
        payload=payload or {},
    )

    for i, step_def in enumerate(steps):
        AutomationStep.objects.create(
            run=run,
            order=i,
            kind=step_def['kind'],
            delay_seconds=step_def.get('delay_seconds', 0),
            content=step_def.get('content'),
            params=step_def.get('params', {}),
        )

    return run, True


def rule_initial_contact(lead):
    """
    When a new lead is assigned: schedule 3-touch cadence.
    Day 0: email draft, Day 3: follow-up, Day 7: final nudge.
    """
    steps = [
        {'kind': C.STEP_KIND_SEND_EMAIL, 'delay_seconds': 0,
         'params': {'template_kind': 'initial_outreach', 'require_approval': True}},
        {'kind': C.STEP_KIND_WAIT, 'delay_seconds': 3 * 24 * 3600},
        {'kind': C.STEP_KIND_SEND_EMAIL, 'delay_seconds': 0,
         'params': {'template_kind': 'follow_up', 'require_approval': True}},
        {'kind': C.STEP_KIND_WAIT, 'delay_seconds': 4 * 24 * 3600},
        {'kind': C.STEP_KIND_SEND_EMAIL, 'delay_seconds': 0,
         'params': {'template_kind': 'follow_up', 'require_approval': True}},
        {'kind': C.STEP_KIND_CREATE_FOLLOWUP, 'delay_seconds': 0,
         'params': {'title': 'Manual check-in', 'days': 10}},
    ]
    return create_run(
        lead=lead, name=f'initial-contact-{lead.pk}',
        steps=steps,
        payload={'kind': 'initial_contact'},
    )


def rule_meeting_followup(lead, meeting):
    """After a meeting: follow-up email in 24h + task in 3 days."""
    steps = [
        {'kind': C.STEP_KIND_WAIT, 'delay_seconds': 24 * 3600},
        {'kind': C.STEP_KIND_SEND_EMAIL, 'delay_seconds': 0,
         'params': {'template_kind': 'meeting_follow_up', 'require_approval': True}},
        {'kind': C.STEP_KIND_WAIT, 'delay_seconds': 2 * 24 * 3600},
        {'kind': C.STEP_KIND_CREATE_TASK, 'delay_seconds': 0,
         'params': {'title': f'Follow up on meeting: {meeting.title}',
                    'priority': 'high'}},
    ]
    return create_run(
        lead=lead, name=f'meeting-followup-{meeting.pk}',
        steps=steps,
        payload={'kind': 'meeting_followup', 'meeting_id': meeting.pk},
    )


def rule_proposal_followup(lead, quotation):
    """After a quotation is sent: follow up in 3 days, then weekly."""
    steps = [
        {'kind': C.STEP_KIND_WAIT, 'delay_seconds': 3 * 24 * 3600},
        {'kind': C.STEP_KIND_SEND_EMAIL, 'delay_seconds': 0,
         'params': {'template_kind': 'proposal_follow_up', 'require_approval': True}},
        {'kind': C.STEP_KIND_WAIT, 'delay_seconds': 4 * 24 * 3600},
        {'kind': C.STEP_KIND_CREATE_TASK, 'delay_seconds': 0,
         'params': {'title': f'Check on proposal: {quotation.title}',
                    'priority': 'high'}},
    ]
    return create_run(
        lead=lead, name=f'proposal-followup-{quotation.pk}',
        steps=steps,
        payload={'kind': 'proposal_followup', 'quotation_id': quotation.pk},
    )


def rule_re_engagement(lead):
    """Dormant lead (60+ days): 1 nudge email + task."""
    steps = [
        {'kind': C.STEP_KIND_SEND_EMAIL, 'delay_seconds': 0,
         'params': {'template_kind': 're_engagement', 'require_approval': True}},
        {'kind': C.STEP_KIND_WAIT, 'delay_seconds': 5 * 24 * 3600},
        {'kind': C.STEP_KIND_CREATE_TASK, 'delay_seconds': 0,
         'params': {'title': 'Re-engagement check-in', 'priority': 'medium'}},
    ]
    return create_run(
        lead=lead, name=f'reengage-{lead.pk}-{timezone.now().date()}',
        steps=steps,
        payload={'kind': 're_engagement'},
    )


def rule_referral_task(lead, referral):
    """New referral: task to thank the referrer."""
    steps = [
        {'kind': C.STEP_KIND_CREATE_TASK, 'delay_seconds': 0,
         'params': {'title': f'Thank referrer for lead {lead.name}',
                    'priority': 'medium'}},
    ]
    return create_run(
        lead=lead, name=f'referral-task-{referral.pk}',
        steps=steps,
        payload={'kind': 'referral_task', 'referral_id': referral.pk},
    )


def rule_upsell_existing_client(lead):
    """Won client (60+ days ago): upsell opportunity."""
    steps = [
        {'kind': C.STEP_KIND_CREATE_TASK, 'delay_seconds': 0,
         'params': {'title': f'Upsell opportunity: {lead.name}',
                    'priority': 'medium'}},
    ]
    return create_run(
        lead=lead, name=f'upsell-{lead.pk}-{timezone.now().date()}',
        steps=steps,
        payload={'kind': 'upsell'},
    )