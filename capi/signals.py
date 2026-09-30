"""Connects existing CRM models to the trigger engine WITHOUT modifying them.
Every handler is fail-safe: a CAPI problem can never break saving a lead/deal."""
import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from leads.models import (Contract, Lead, Meeting, Opportunity, Payment, Quotation)

from . import constants as C
from .triggers import fire

logger = logging.getLogger(__name__)


def _old(instance, *fields):
    if not instance.pk:
        return None
    return instance.__class__.objects.filter(pk=instance.pk).values(*fields).first()


def _safe(fn):
    def wrapper(sender, instance, **kwargs):
        try:
            fn(sender, instance, **kwargs)
        except Exception:
            logger.exception('CAPI signal handler failed for %s', sender.__name__)
    wrapper.__name__ = fn.__name__
    return wrapper


def _later(fn):
    """Run after the surrounding transaction commits (tracking rows exist by then)."""
    def run():
        try:
            fn()
        except Exception:
            logger.exception('CAPI trigger failed')
    transaction.on_commit(run)


# ---------------------------------------------------------------- lead resolution
def lead_for_opportunity(opp):
    return opp.lead or (opp.contact.lead if opp.contact_id else None)


def lead_for_meeting(m):
    return m.lead or (lead_for_opportunity(m.opportunity) if m.opportunity_id else None) \
        or (m.contact.lead if m.contact_id else None)


def lead_for_quotation(q):
    return q.lead or (lead_for_opportunity(q.opportunity) if q.opportunity_id else None) \
        or (q.contact.lead if q.contact_id else None)


def lead_for_contract(c):
    return (lead_for_opportunity(c.opportunity) if c.opportunity_id else None) \
        or (c.contact.lead if c.contact_id else None)


# ---------------------------------------------------------------- Lead
@receiver(pre_save, sender=Lead, dispatch_uid='capi_lead_pre')
@_safe
def lead_pre(sender, instance, **kw):
    old = _old(instance, 'status', 'ai_score')
    instance._capi_old = old


@receiver(post_save, sender=Lead, dispatch_uid='capi_lead_post')
@_safe
def lead_post(sender, instance, created, **kw):
    old = getattr(instance, '_capi_old', None) or {}
    old_status, old_score = old.get('status', ''), old.get('ai_score', 0)
    lead = instance

    def go():
        if created:
            fire(C.TRIGGER_LEAD_CREATED, lead, dedup_key=f'lead:{lead.pk}:created',
                 source_model='Lead', source_id=lead.pk, old_score=0)
        if not created and old_status != lead.status:
            fire(C.TRIGGER_LEAD_STATUS, lead, dedup_key=f'lead:{lead.pk}:status:{lead.status}',
                 source_model='Lead', source_id=lead.pk, from_value=old_status, to_value=lead.status)
            if lead.status == 'converted':
                fire(C.TRIGGER_DEAL_WON, lead, dedup_key=f'lead:{lead.pk}:won',
                     source_model='Lead', source_id=lead.pk, value=lead.quotation or None)
        if (old_score or 0) != (lead.ai_score or 0):
            fire(C.TRIGGER_LEAD_SCORE, lead, dedup_key=f'lead:{lead.pk}:score',
                 source_model='Lead', source_id=lead.pk, old_score=old_score or 0)
    _later(go)


# ---------------------------------------------------------------- Opportunity
@receiver(pre_save, sender=Opportunity, dispatch_uid='capi_opp_pre')
@_safe
def opp_pre(sender, instance, **kw):
    instance._capi_old = _old(instance, 'stage')


@receiver(post_save, sender=Opportunity, dispatch_uid='capi_opp_post')
@_safe
def opp_post(sender, instance, created, **kw):
    old_stage = (getattr(instance, '_capi_old', None) or {}).get('stage', '')
    if not created and old_stage == instance.stage:
        return
    opp = instance

    def go():
        lead = lead_for_opportunity(opp)
        if not lead:
            return
        fire(C.TRIGGER_OPP_STAGE, lead, dedup_key=f'lead:{lead.pk}:opp_stage:{opp.stage}',
             source_model='Opportunity', source_id=opp.pk, from_value=old_stage, to_value=opp.stage,
             value=opp.value)
        if opp.stage == 'won':
            fire(C.TRIGGER_DEAL_WON, lead, dedup_key=f'lead:{lead.pk}:won',
                 source_model='Opportunity', source_id=opp.pk, value=opp.value)
    _later(go)


# ---------------------------------------------------------------- Meeting (appointment)
@receiver(post_save, sender=Meeting, dispatch_uid='capi_meeting_post')
@_safe
def meeting_post(sender, instance, created, **kw):
    if not created:
        return
    m = instance

    def go():
        lead = lead_for_meeting(m)
        if lead:
            fire(C.TRIGGER_MEETING, lead, dedup_key=f'lead:{lead.pk}:appointment',
                 source_model='Meeting', source_id=m.pk)
    _later(go)


# ---------------------------------------------------------------- Quotation approved = proposal accepted
@receiver(pre_save, sender=Quotation, dispatch_uid='capi_quote_pre')
@_safe
def quote_pre(sender, instance, **kw):
    instance._capi_old = _old(instance, 'status')


@receiver(post_save, sender=Quotation, dispatch_uid='capi_quote_post')
@_safe
def quote_post(sender, instance, created, **kw):
    old = (getattr(instance, '_capi_old', None) or {}).get('status', '')
    if instance.status != 'approved' or old == 'approved':
        return
    q = instance

    def go():
        lead = lead_for_quotation(q)
        if lead:
            fire(C.TRIGGER_QUOTE_APPROVED, lead, dedup_key=f'lead:{lead.pk}:proposal_accepted',
                 source_model='Quotation', source_id=q.pk, value=q.total_amount())
    _later(go)


# ---------------------------------------------------------------- Contract signed
@receiver(pre_save, sender=Contract, dispatch_uid='capi_contract_pre')
@_safe
def contract_pre(sender, instance, **kw):
    instance._capi_old = _old(instance, 'status')


@receiver(post_save, sender=Contract, dispatch_uid='capi_contract_post')
@_safe
def contract_post(sender, instance, created, **kw):
    old = (getattr(instance, '_capi_old', None) or {}).get('status', '')
    if instance.status not in ('signed', 'active') or old in ('signed', 'active'):
        return
    c = instance

    def go():
        lead = lead_for_contract(c)
        if lead:
            fire(C.TRIGGER_CONTRACT_SIGNED, lead, dedup_key=f'lead:{lead.pk}:contract',
                 source_model='Contract', source_id=c.pk, value=c.value)
    _later(go)


# ---------------------------------------------------------------- Payment = revenue recorded
@receiver(post_save, sender=Payment, dispatch_uid='capi_payment_post')
@_safe
def payment_post(sender, instance, created, **kw):
    if not created:
        return
    p = instance

    def go():
        fire(C.TRIGGER_REVENUE, p.lead, dedup_key=f'payment:{p.pk}',
             source_model='Payment', source_id=p.pk, value=p.amount)
    _later(go)
