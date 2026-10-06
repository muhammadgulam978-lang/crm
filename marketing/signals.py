import logging
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


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