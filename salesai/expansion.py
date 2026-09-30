"""
Feature 2: expansion opportunities for EXISTING clients (won deals), rules only.

Signals we use, all from the CRM:
  * which SIS products the customer ALREADY owns (from won opportunities + their approved KB match)
  * which approved products they do NOT own yet whose keywords match their verified need / requirements
  * usage / scale facts ("4 campuses", "200 staff") vs. what the current product covers
  * renewal dates on existing won opportunities
  * dormancy: no inbound communication for N days
  * satisfaction / objection signals: verified facts of type 'objection' that are NOT resolved
The AI may only PROPOSE which approved product fits which signal; the rules confirm and dedupe.
A salesperson accepts, edits or dismisses. Nothing is sent automatically.
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import (ASSUMED, VERIFIED, CustomerFact, ExpansionOpportunity, LeadInsight, SISService,
                     normalize_text)
from .services import primary_opportunity, service_catalogue

RENEWAL_WINDOW_DAYS = getattr(settings, 'SALESAI_RENEWAL_WINDOW_DAYS', 90)
DORMANT_DAYS = getattr(settings, 'SALESAI_DORMANT_DAYS', 120)


def owned_services(lead):
    """Approved SIS services the customer has already bought (from won opportunities + their match)."""
    from leads.models import Opportunity
    owned = set()
    for opp in Opportunity.objects.filter(lead=lead, stage='won'):
        m = getattr(opp, 'sales_offer_match', None)
        if m and m.get('primary'):
            owned.add(m['primary']['id'])
        # also the AI match on the insight if the opportunity was won after a match
    insight = LeadInsight.objects.filter(lead=lead).first()
    if insight and insight.offer_match and insight.offer_match.get('primary'):
        # only count it as owned if there's a won opportunity
        if Opportunity.objects.filter(lead=lead, stage='won').exists():
            owned.add(insight.offer_match['primary']['id'])
    return owned


def _recent_inbound(lead, days=DORMANT_DAYS):
    from leads.models import CommunicationLog, EmailLog, Meeting
    since = timezone.now() - timedelta(days=days)
    if CommunicationLog.objects.filter(lead=lead, direction='inbound', created_at__gte=since).exists():
        return True
    if EmailLog.objects.filter(lead=lead, direction='inbound', created_at__gte=since).exists():
        return True
    if Meeting.objects.filter(lead=lead, status='completed', scheduled_at__gte=since).exists():
        return True
    return False


def _renewal_due(lead):
    from leads.models import Opportunity
    today = timezone.localdate()
    horizon = today + timedelta(days=RENEWAL_WINDOW_DAYS)
    for opp in Opportunity.objects.filter(lead=lead, stage='won'):
        rd = getattr(opp, 'renewal_date', None)
        if rd and today <= rd <= horizon:
            return opp, rd
    return None, None


def _suggest(lead, kind, service, reason, evidence, confidence='medium', value=None):
    key = f'{kind}:{lead.pk}:{service.pk if service else "custom"}'
    if ExpansionOpportunity.objects.filter(lead=lead, dedup_key=key).exclude(status='dismissed').exists():
        return None
    return ExpansionOpportunity.objects.create(
        lead=lead, kind=kind, service=service,
        service_label=service.offer_label if service else 'Custom Software Discovery',
        reason=reason[:2000], evidence=evidence, confidence=confidence,
        estimated_value=value, dedup_key=key, created_by=None)


def analyse_client(lead):
    """Returns the list of suggestions (new + existing open ones) for this won client."""
    from leads.models import Opportunity
    if not Opportunity.objects.filter(lead=lead, stage='won').exists():
        return []
    catalogue = service_catalogue()                # only approved, active
    owned_ids = owned_services(lead)
    owned_names = {s['name'] for s in catalogue if s['id'] in owned_ids}
    facts = list(CustomerFact.objects.filter(