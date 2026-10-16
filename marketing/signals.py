"""
Marketing signals — react to CRM events without modifying them.

Every handler is fail-safe: a marketing problem can never break a CRM save.
All handlers are idempotent: safe to call multiple times.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)


# ============================================================================
# SIGNAL 1 — Sync Lead.lead_source to ProspectSource (existing)
# ============================================================================
@receiver(post_save, sender='leads.Lead', dispatch_uid='marketing_lead_source_sync')
def sync_lead_source(sender, instance, created, **kwargs):
    try:
        if not created or instance.marketing_source_id:
            return
        from .models import ProspectSource
        KIND_MAP = {
            'personal_reference': 'referral',
            'client_reference': 'referral',
            'staff_reference': 'referral',
            'website': 'organic',
            'facebook': 'social',
            'whatsapp': 'social',
        }
        kind = KIND_MAP.get(instance.lead_source)
        if not kind:
            return
        src = ProspectSource.objects.filter(kind=kind, is_active=True).order_by('id').first()
        if src:
            type(instance).objects.filter(pk=instance.pk, marketing_source__isnull=True)\
                .update(marketing_source=src)
    except Exception:
        logger.exception('marketing lead source sync failed for lead %s', instance.pk)


# ============================================================================
# SIGNAL 2 — Auto-assign new leads + start initial cadence
# ============================================================================
@receiver(post_save, sender='leads.Lead', dispatch_uid='marketing_lead_created_autostart')
def on_lead_created(sender, instance, created, **kwargs):
    """
    When a new lead arrives:
      1. Auto-assign to an SPO (if none)
      2. Create LeadStage row (default: 'new')
      3. Start initial contact cadence (if SPO assigned)
    """
    if not created:
        return
    if instance.is_duplicate:
        return

    try:
        from .models import LeadStage
        from .services import assignment as asn
        from .services import followup_rules as fr

        # Always create stage row
        LeadStage.objects.get_or_create(lead=instance)

        # Auto-assign (no-op if already assigned)
        asn.assign_lead(instance)

        # Start initial cadence if SPO is assigned
        if instance.spo_id:
            fr.rule_initial_contact(instance)
    except Exception:
        logger.exception('Failed to bootstrap lead %s', instance.pk)


# ============================================================================
# SIGNAL 3 — Handle inbound messages (reply + opt-out detection)
# ============================================================================
@receiver(post_save, sender='leads.CommunicationLog',
          dispatch_uid='marketing_inbound_handler')
def on_inbound_communication(sender, instance, created, **kwargs):
    """
    When an inbound message is logged:
      - Classify as opt_out / positive / neutral
      - Pause automation for opt_out or positive (human takeover)
      - Notify the assigned salesperson
    """
    if not created:
        return
    if instance.direction != 'inbound':
        return
    if instance.comm_type not in ('email', 'whatsapp', 'sms', 'call'):
        return
    if not instance.lead_id:
        return

    try:
        from .services import reply_handler as rh
        text = ((instance.body or '') + ' ' + (instance.subject or '')).strip()
        result = rh.handle_inbound_message(
            instance.lead,
            text=text,
            source=f'comm_{instance.pk}',
        )
        logger.info('Reply handled for lead %s: %s', instance.lead_id, result)
    except Exception:
        logger.exception('Failed to handle inbound message for lead %s', instance.lead_id)


# ============================================================================
# SIGNAL 4 — Meeting created -> follow-up cadence
# ============================================================================
@receiver(post_save, sender='leads.Meeting', dispatch_uid='marketing_meeting_followup')
def on_meeting_created(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from .services import followup_rules as fr
        lead = instance.lead or (
            instance.opportunity.lead if instance.opportunity_id else None
        )
        if not lead:
            return
        fr.rule_meeting_followup(lead, instance)

        # Move stage to 'meeting' if applicable
        from .models import LeadStage
        from . import constants as C
        stage_obj, _ = LeadStage.objects.get_or_create(lead=lead)
        if stage_obj.stage in (C.STAGE_NEW, C.STAGE_RESEARCHED,
                               C.STAGE_QUALIFIED, C.STAGE_CONTACTED, C.STAGE_REPLIED):
            stage_obj.set_stage(C.STAGE_MEETING, reason=f'Meeting: {instance.title}')
    except Exception:
        logger.exception('Meeting followup failed for meeting %s', instance.pk)


# ============================================================================
# SIGNAL 5 — Quotation sent -> proposal follow-up cadence
# ============================================================================
@receiver(post_save, sender='leads.Quotation', dispatch_uid='marketing_proposal_followup')
def on_quotation_sent(sender, instance, created, **kwargs):
    if instance.status != 'sent':
        return
    try:
        from .services import followup_rules as fr
        lead = instance.lead or (
            instance.opportunity.lead if instance.opportunity_id else None
        )
        if not lead:
            return
        fr.rule_proposal_followup(lead, instance)

        # Move stage
        from .models import LeadStage
        from . import constants as C
        stage_obj, _ = LeadStage.objects.get_or_create(lead=lead)
        if stage_obj.stage not in (C.STAGE_WON, C.STAGE_LOST, C.STAGE_NEGOTIATION):
            stage_obj.set_stage(C.STAGE_PROPOSAL, reason=f'Quotation: {instance.title}')
    except Exception:
        logger.exception('Proposal followup failed for quotation %s', instance.pk)


# ============================================================================
# SIGNAL 6 — Referral created -> thank-referrer task
# ============================================================================
@receiver(post_save, sender='marketing.Referral', dispatch_uid='marketing_referral_task')
def on_referral_created(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from .services import followup_rules as fr
        lead = instance.referred_lead or instance.referrer_lead
        if not lead:
            return
        fr.rule_referral_task(lead, instance)
    except Exception:
        logger.exception('Referral task failed for referral %s', instance.pk)


# ============================================================================
# SIGNAL 7 — Payment recorded -> upsell opportunity for won clients
# ============================================================================
@receiver(post_save, sender='leads.Payment', dispatch_uid='marketing_upsell_from_payment')
def on_payment_recorded(sender, instance, created, **kwargs):
    """
    When a won client makes a payment, create an upsell opportunity task.
    Only fires once per lead per 90 days.
    """
    if not created:
        return
    if not instance.lead_id:
        return

    try:
        from .models import LeadStage, AutomationRun
        from .services import followup_rules as fr
        from . import constants as C

        lead = instance.lead
        stage_obj, _ = LeadStage.objects.get_or_create(lead=lead)

        # Mark stage as won if not already
        if stage_obj.stage != C.STAGE_WON:
            stage_obj.set_stage(C.STAGE_WON, reason=f'Payment received: {instance.amount}')

        # Upsell: only if no active upsell in last 90 days
        cutoff = timezone.now() - timezone.timedelta(days=90)
        recent = AutomationRun.objects.filter(
            lead=lead,
            name__startswith=f'upsell-{lead.pk}-',
            created_at__gte=cutoff,
        ).exists()
        if not recent:
            fr.rule_upsell_existing_client(lead)
    except Exception:
        logger.exception('Upsell task failed for payment %s', instance.pk)