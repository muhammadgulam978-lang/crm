"""Find dormant / lost / stale leads for reactivation."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from leads.models import Lead
from marketing.models import MarketingConsent, SuppressionEntry


ACTIVE_STATUSES = ['converted']


def _base_qs(days_dormant=60):
    """Leads that have marketing_source set and haven't been touched in N days."""
    cutoff = timezone.now() - timedelta(days=days_dormant)
    return Lead.objects.filter(
        marketing_source__isnull=False,
    ).exclude(
        status__in=ACTIVE_STATUSES,
    ).filter(
        Q(updated_at__lt=cutoff) | Q(updated_at__isnull=True),
        is_duplicate=False,
    ).select_related('marketing_source', 'interested_service', 'spo')


def dormant_leads(days=60):
    return _base_qs(days).order_by('-ai_score', 'updated_at')


def lost_leads():
    return Lead.objects.filter(status='lost', marketing_source__isnull=False)\
        .exclude(is_duplicate=True).order_by('-updated_at')


def stale_opportunity_leads(days=45):
    """Leads whose last Opportunity is old but not won/lost."""
    cutoff = timezone.now() - timedelta(days=days)
    return Lead.objects.filter(
        opportunities__updated_at__lt=cutoff,
        opportunities__stage__in=['new', 'contacted', 'qualified'],
    ).exclude(is_duplicate=True).distinct().order_by('-opportunities__value')


def reactivation_candidates(days=60):
    """Union of dormant + lost, deduped."""
    from itertools import chain
    seen = set()
    out = []
    for lead in chain(dormant_leads(days), lost_leads()):
        if lead.pk in seen:
            continue
        seen.add(lead.pk)
        out.append(lead)
    return out


def last_communication(lead):
    """Latest CommunicationLog / EmailLog / CallLog for the lead."""
    from leads.models import CommunicationLog, EmailLog, CallLog
    candidates = []
    c = CommunicationLog.objects.filter(lead=lead).order_by('-created_at').first()
    if c:
        candidates.append(('communication', c.created_at, c))
    e = EmailLog.objects.filter(lead=lead).order_by('-created_at').first()
    if e:
        candidates.append(('email', e.created_at, e))
    call = CallLog.objects.filter(lead=lead).order_by('-created_at').first()
    if call:
        candidates.append(('call', call.created_at, call))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[1], reverse=True)
    return candidates[0]


def recommended_next_action(lead):
    """Rule-based — no AI. Returns (action, reason)."""
    if lead.status == 'lost':
        return ('Send reactivation email', 'Lead was marked lost.')
    if lead.ai_score >= 70:
        return ('Call within 24h', 'High AI score but no recent activity.')
    if lead.ai_score >= 40:
        return ('Send follow-up email', 'Medium score, worth a gentle nudge.')
    return ('Move to nurture sequence', 'Low score — nurture, do not push.')