"""
Marketing analytics aggregation for the dashboard.

Rules:
  * Never fabricate data — every number comes from a real ORM query.
  * Zero-time-safe: all functions handle empty tables gracefully.
  * Single source of truth: this module is the ONLY place dashboard numbers come from.
"""
from datetime import timedelta
from django.db.models import Count, Q, Sum
from django.utils import timezone

from leads.models import (
    Lead, Meeting, Quotation, Payment, FollowUp, CommunicationLog,
)
from marketing import constants as C
from marketing.models import (
    Campaign, ProspectSource, MarketingContent, Referral,
    MarketingConsent, SuppressionEntry, AutomationRun, LeadStage,
)
from marketing.services import qualify


def _since(days):
    return timezone.now() - timedelta(days=days)


# ============================================================================
# KPI CARDS
# ============================================================================
def kpis(days=30):
    """Main KPI numbers for the dashboard cards."""
    since = _since(days)
    leads = Lead.objects.filter(created_at__gte=since)
    total = leads.count()

    # HOT/WARM/COLD
    hot = leads.filter(ai_score__gte=C.HOT_THRESHOLD).count()
    warm = leads.filter(
        ai_score__gte=C.WARM_THRESHOLD, ai_score__lt=C.HOT_THRESHOLD
    ).count()
    cold = leads.filter(
        ai_score__gt=0, ai_score__lt=C.WARM_THRESHOLD
    ).count()
    unknown = leads.filter(Q(ai_score=0) | Q(ai_score__isnull=True)).count()

    # Stage counts (from LeadStage)
    stage_qs = LeadStage.objects.filter(lead__created_at__gte=since)
    stages = dict(
        stage_qs.values_list('stage').annotate(n=Count('id'))
    )

    # Won / Lost
    won = stages.get(C.STAGE_WON, 0)
    lost = stages.get(C.STAGE_LOST, 0)

    # Reactivation candidates
    from marketing.services import reactivation as react
    try:
        react_count = len(react.reactivation_candidates(days=60))
    except Exception:
        react_count = 0

    # Automation
    auto_active = AutomationRun.objects.filter(
        status__in=[C.AUTOMATION_STATUS_PENDING, C.AUTOMATION_STATUS_RUNNING],
    ).count()
    auto_done = AutomationRun.objects.filter(
        status=C.AUTOMATION_STATUS_DONE,
        finished_at__gte=since,
    ).count()

    # Meetings, Proposals
    meetings = Meeting.objects.filter(created_at__gte=since).count()
    proposals = Quotation.objects.filter(created_at__gte=since).count()

    # Revenue
    revenue = Payment.objects.filter(date__gte=since).aggregate(
        total=Sum('amount')
    )['total'] or 0

    # ---- Replies (inbound messages in period) ----
    replies = CommunicationLog.objects.filter(
        direction='inbound',
        created_at__gte=since,
        lead__isnull=False,
    ).count()

    # ---- Message drafts (from MessageDraft model if it exists) ----
    message_stats = _message_stats(since)

    # Conversion rate
    conversion_rate = round(100 * won / total, 1) if total else 0

    return {
        'period_days': days,
        'total_leads': total,
        'new_prospects': Lead.objects.filter(
            created_at__gte=since,
            marketing_source__isnull=True,
        ).count(),
        'marketing_leads': Lead.objects.filter(
            created_at__gte=since,
            marketing_source__isnull=False,
        ).count(),
        'hot': hot, 'warm': warm, 'cold': cold, 'unknown': unknown,
        'qualified': stages.get(C.STAGE_QUALIFIED, 0),
        'contacted': stages.get(C.STAGE_CONTACTED, 0),
        'replied': stages.get(C.STAGE_REPLIED, 0),
        'won': won, 'lost': lost,
        'reactivation_count': react_count,
        'automation_active': auto_active,
        'automation_done': auto_done,
        'meetings': meetings,
        'proposals': proposals,
        'revenue': revenue,
        'conversion_rate': conversion_rate,
        'referrals_pending': Referral.objects.filter(
            status=C.REFERRAL_STATUS_PENDING
        ).count(),
        'followups_pending': FollowUp.objects.filter(status='pending').count(),
        'consent_granted': MarketingConsent.objects.filter(
            status=C.CONSENT_GRANTED
        ).count(),
        'suppressed': SuppressionEntry.objects.count(),

        # ---- NEW: Replies KPI ----
        'replies': replies,

        # ---- NEW: Message draft KPIs ----
        'messages_drafted': message_stats['drafted'],
        'messages_approved': message_stats['approved'],
        'messages_sent': message_stats['sent'],
        'messages_rejected': message_stats['rejected'],
        'messages_total': message_stats['total'],
    }


def _message_stats(since):
    """
    Message draft counts. Safe if MessageDraft model doesn't exist yet
    (returns zeros so the dashboard never breaks).
    """
    try:
        from marketing.models import MessageDraft
    except ImportError:
        return {'drafted': 0, 'approved': 0, 'sent': 0, 'rejected': 0, 'total': 0}

    qs = MessageDraft.objects.filter(created_at__gte=since)
    return {
        'drafted': qs.filter(status=getattr(C, 'DRAFT_STATUS_DRAFT', 'draft')).count(),
        'approved': qs.filter(status=getattr(C, 'DRAFT_STATUS_APPROVED', 'approved')).count(),
        'sent': qs.filter(status=getattr(C, 'DRAFT_STATUS_SENT', 'sent')).count(),
        'rejected': qs.filter(status=getattr(C, 'DRAFT_STATUS_REJECTED', 'rejected')).count(),
        'total': qs.count(),
    }


# ============================================================================
# LEAD-SOURCE ANALYTICS
# ============================================================================
def source_performance(days=30, limit=15):
    """Leads per source + conversion rate per source."""
    since = _since(days)

    rows = []
    sources = ProspectSource.objects.filter(is_active=True).annotate(
        total=Count('leads', filter=Q(leads__created_at__gte=since)),
        won=Count(
            'leads',
            filter=Q(
                leads__created_at__gte=since,
                leads__marketing_stage__stage=C.STAGE_WON,
            ),
        ),
    ).order_by('-total')[:limit]

    for s in sources:
        rate = round(100 * s.won / s.total, 1) if s.total else 0
        rows.append({
            'id': s.pk,
            'name': s.name,
            'kind': s.get_kind_display(),
            'total': s.total,
            'won': s.won,
            'conversion_rate': rate,
        })
    return rows


# ============================================================================
# CONVERSION FUNNEL
# ============================================================================
def conversion_funnel(days=30):
    """Count of leads at each stage — displayed as a funnel."""
    since = _since(days)
    stage_order = [
        C.STAGE_NEW, C.STAGE_RESEARCHED, C.STAGE_QUALIFIED,
        C.STAGE_CONTACTED, C.STAGE_REPLIED, C.STAGE_MEETING,
        C.STAGE_PROPOSAL, C.STAGE_NEGOTIATION, C.STAGE_WON,
    ]
    counts = dict(
        LeadStage.objects.filter(lead__created_at__gte=since)
        .values_list('stage')
        .annotate(n=Count('id'))
    )

    peak = max([counts.get(s, 0) for s in stage_order] + [1])

    funnel = []
    for s in stage_order:
        label = dict(C.STAGE_CHOICES).get(s, s)
        n = counts.get(s, 0)
        pct = int(100 * n / peak) if peak else 0
        funnel.append({
            'stage': s,
            'label': label,
            'count': n,
            'pct_width': max(pct, 3) if n else 0,
        })
    return funnel


# ============================================================================
# LOST REASONS
# ============================================================================
def lost_reasons(days=90, limit=10):
    """Group lost leads by reason."""
    since = _since(days)
    qs = LeadStage.objects.filter(
        stage=C.STAGE_LOST,
        entered_at__gte=since,
    ).values_list('reason', flat=True)

    from collections import Counter
    counter = Counter()
    for reason in qs:
        key = (reason or 'Not specified')[:60]
        counter[key] += 1

    return [
        {'reason': r, 'count': n}
        for r, n in counter.most_common(limit)
    ]


# ============================================================================
# CONTENT PERFORMANCE
# ============================================================================
def content_performance(days=30, limit=10):
    """Content pieces published."""
    since = _since(days)
    qs = MarketingContent.objects.filter(
        published_at__gte=since,
    ).select_related('campaign').order_by('-published_at')[:limit]

    rows = []
    for c in qs:
        rows.append({
            'title': c.title,
            'kind': c.get_kind_display(),
            'campaign': c.campaign.name if c.campaign else '—',
            'published_at': c.published_at,
            'url': c.published_url or '',
        })
    return rows


# ============================================================================
# HOT LEADS TABLE
# ============================================================================
def hot_leads(days=30, limit=15):
    """Recent HOT leads needing attention."""
    since = _since(days)
    leads = Lead.objects.filter(
        ai_score__gte=C.HOT_THRESHOLD,
        created_at__gte=since,
        is_duplicate=False,
    ).select_related('spo', 'marketing_source').order_by('-ai_score', '-created_at')[:limit]

    rows = []
    for l in leads:
        stage_obj = getattr(l, 'marketing_stage', None)
        rows.append({
            'id': l.pk,
            'name': l.name,
            'email': l.email or '',
            'phone': l.contact_number or '',
            'score': l.ai_score,
            'stage': stage_obj.get_stage_display() if stage_obj else '—',
            'source': l.marketing_source.name if l.marketing_source else '—',
            'spo': l.spo.name if l.spo else '—',
            'created_at': l.created_at,
        })
    return rows


# ============================================================================
# REACTIVATION OPPORTUNITIES TABLE
# ============================================================================
def reactivation_table(days=60, limit=10):
    """Reactivation candidates with recommended actions."""
    from marketing.services import reactivation as react
    try:
        candidates = react.reactivation_candidates(days=days)[:limit]
    except Exception:
        return []

    rows = []
    for lead in candidates:
        last = react.last_communication(lead)
        action, reason = react.recommended_next_action(lead)
        rows.append({
            'id': lead.pk,
            'name': lead.name,
            'email': lead.email or '',
            'score': lead.ai_score,
            'last_contact': last[1] if last else None,
            'action': action,
            'reason': reason,
        })
    return rows


# ============================================================================
# REVENUE BY SOURCE (NEW)
# ============================================================================
def revenue_by_source(days=30, limit=15):
    """
    Aggregate payment revenue per marketing source.
    Only counts leads that have a marketing_source FK set.
    """
    since = _since(days)
    rows = (
        Payment.objects
        .filter(
            date__gte=since,
            lead__marketing_source__isnull=False,
        )
        .values('lead__marketing_source__name')
        .annotate(
            total=Sum('amount'),
            payment_count=Count('id'),
        )
        .order_by('-total')[:limit]
    )
    return [
        {
            'source': r['lead__marketing_source__name'],
            'total': r['total'],
            'count': r['payment_count'],
        }
        for r in rows
    ]


# ============================================================================
# MESSAGE DRAFT LIST (helper for dashboard detail)
# ============================================================================
def recent_drafts(limit=10):
    """Latest message drafts needing review."""
    try:
        from marketing.models import MessageDraft
    except ImportError:
        return []
    qs = MessageDraft.objects.filter(
        status=getattr(C, 'DRAFT_STATUS_DRAFT', 'draft'),
    ).select_related('lead').order_by('-created_at')[:limit]
    return [
        {
            'id': d.pk,
            'lead_id': d.lead_id,
            'lead_name': d.lead.name if d.lead else '—',
            'subject': d.subject or '(no subject)',
            'kind': d.kind,
            'created_at': d.created_at,
        }
        for d in qs
    ]