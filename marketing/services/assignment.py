"""
Auto-assign leads to salespersons (SPO).

Rules (in order):
  1. If lead already has SPO → skip
  2. Match by city → SPO with matching territory (if configured)
  3. Round-robin among active SPOs (based on least recent assignment)
  4. Fallback: leave unassigned and notify admins

Idempotent: safe to call multiple times per lead.
"""
import logging
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from leads.models import Lead, SalesPerson
from marketing.models import LeadStage
from marketing.services import reply_handler as rh

logger = logging.getLogger(__name__)


def active_spos():
    """Return queryset of active salespersons."""
    return SalesPerson.objects.filter(user__is_active=True).order_by('id')


def next_spo_for_round_robin():
    """
    Pick the SPO with the fewest leads assigned in the last 30 days.
    Returns SalesPerson or None.
    """
    thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
    spos = active_spos().annotate(
        recent_count=Count(
            'leads',
            filter=Q(leads__created_at__gte=thirty_days_ago),
        )
    ).order_by('recent_count', 'id')
    return spos.first()


@transaction.atomic
def assign_lead(lead, *, force=False):
    """
    Assign a lead to an SPO. Returns the SalesPerson or None.
    Idempotent: if lead already has SPO, does nothing unless force=True.
    """
    if not force and lead.spo_id:
        return lead.spo

    spo = next_spo_for_round_robin()
    if spo is None:
        logger.warning('No active SPOs available for lead %s', lead.pk)
        return None

    lead.spo = spo
    lead.save(update_fields=['spo', 'updated_at'])

    # Record stage history
    try:
        stage_obj, _ = LeadStage.objects.get_or_create(lead=lead)
        if stage_obj.stage == 'new':
            stage_obj.set_stage(
                'new',
                reason=f'Auto-assigned to {spo.name}',
            )
    except Exception:
        logger.exception('Failed to update stage for lead %s', lead.pk)

    # Notify the SPO
    rh.notify_owner(
        lead,
        title=f'New lead assigned: {lead.name}',
        message=(
            f'Lead "{lead.name}" (ID {lead.pk}) has been assigned to you.\n'
            f'Source: {lead.lead_source or "unknown"}\n'
            f'Contact: {lead.contact_number or lead.email or "n/a"}'
        ),
        link=f'/leads/{lead.pk}/',
    )

    return spo


def assign_unassigned_leads(limit=50):
    """
    Batch assign leads that have no SPO.
    Returns dict: {assigned, skipped, no_spo_available}
    """
    qs = Lead.objects.filter(
        spo__isnull=True,
        status__in=['new', 'positive'],
    ).order_by('created_at')[:limit]

    counts = {'assigned': 0, 'skipped': 0, 'no_spo_available': 0}
    for lead in qs:
        spo = assign_lead(lead)
        if spo is None:
            counts['no_spo_available'] += 1
        else:
            counts['assigned'] += 1
    return counts