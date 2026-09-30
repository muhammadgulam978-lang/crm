"""
Triggers:
  * a new Lead  -> an insight row is created, the rules run at once (no AI needed to see what is missing),
                   and the AI review runs in the background (if enabled).
  * an Opportunity / Meeting / Communication change -> the rules are re-evaluated (cheap, no AI).
The AI never sends anything to a customer and never changes a CRM record by itself.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from leads.models import CommunicationLog, Lead, Meeting, Opportunity

logger = logging.getLogger('salesai')


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception:
        logger.exception('salesai signal handler failed (CRM operation unaffected)')


@receiver(post_save, sender=Lead)
def on_lead_saved(sender, instance, created, raw=False, **kw):
    if raw or not created:
        return
    from .models import LeadInsight, SalesAssistantSettings
    from .services import auto_analysis_allowed, queue_background_review, recompute_rules

    def run():
        lead = Lead.objects.filter(pk=instance.pk).first()
        if not lead:
            return
        LeadInsight.objects.get_or_create(lead=lead)
        recompute_rules(lead)
        cfg = SalesAssistantSettings.get()
        if cfg.ai_enabled and cfg.auto_analyze_new_leads and auto_analysis_allowed() and not lead.is_duplicate:
            queue_background_review(lead.pk)

    transaction.on_commit(lambda: _safe(run))


def _refresh(lead_id):
    from .models import LeadInsight
    from .services import recompute_rules
    lead = Lead.objects.filter(pk=lead_id).first()
    if lead and LeadInsight.objects.filter(lead=lead).exists():
        recompute_rules(lead)


@receiver(post_save, sender=Opportunity)
def on_opportunity_saved(sender, instance, raw=False, **kw):
    if not raw and instance.lead_id:
        transaction.on_commit(lambda: _safe(_refresh, instance.lead_id))


@receiver(post_save, sender=Meeting)
def on_meeting_saved(sender, instance, raw=False, **kw):
    if not raw and instance.lead_id:
        transaction.on_commit(lambda: _safe(_refresh, instance.lead_id))


@receiver(post_save, sender=CommunicationLog)
def on_comm_saved(sender, instance, created, raw=False, **kw):
    if not raw and created and instance.lead_id:
        transaction.on_commit(lambda: _safe(_refresh, instance.lead_id))
