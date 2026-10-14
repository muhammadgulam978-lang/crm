"""
Weekly marketing report — aggregates the last 7 days and emails a summary.

No new models. Reads existing Lead, ProspectSource, Campaign, Referral,
MarketingConsent, SuppressionEntry, and capi.LeadTouchpoint.

Usage:
    from marketing.services import weekly_report as wr
    data = wr.collect(days=7)
    html = wr.render_html(data)
    wr.send(recipients=['admin@example.com'], days=7)
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import Count, Q, Sum
from django.template.loader import render_to_string
from django.utils import timezone

from leads.models import Lead, Opportunity, Payment, SalesPerson
from marketing import constants as C
from marketing.models import (
    ProspectSource, Campaign, Referral, MarketingConsent,
    SuppressionEntry, MarketingContent,
)
from marketing.services import qualify

logger = logging.getLogger(__name__)


def _since(days):
    return timezone.now() - timedelta(days=days)


def collect(days=7):
    """Return a dict with every aggregate the report needs. No email side-effects."""
    since = _since(days)
    today = timezone.localdate()

    # ---- Base querysets ----
    leads_7d = Lead.objects.filter(created_at__gte=since)
    leads_30d = Lead.objects.filter(created_at__gte=_since(30))

    # ---- Executive summary ----
    total_leads = leads_7d.count()
    hot_leads = leads_7d.filter(ai_score__gte=C.HOT_THRESHOLD).count()
    warm_leads = leads_7d.filter(
        ai_score__gte=C.WARM_THRESHOLD, ai_score__lt=C.HOT_THRESHOLD
    ).count()
    cold_leads = leads_7d.filter(
        ai_score__gt=0, ai_score__lt=C.WARM_THRESHOLD
    ).count()
    unknown_leads = leads_7d.filter(Q(ai_score=0) | Q(ai_score__isnull=True)).count()

    converted_7d = leads_7d.filter(status='converted').count()
    lost_7d = leads_7d.filter(status='lost').count()

    # Revenue from payments in window
    revenue_7d = Payment.objects.filter(
        date__gte=since
    ).aggregate(total=Sum('amount'))['total'] or 0

    # ---- Leads by source ----
    by_source = (
        leads_7d
        .filter(marketing_source__isnull=False)
        .values('marketing_source__name', 'marketing_source__kind')
        .annotate(n=Count('id'))
        .order_by('-n')[:10]
    )

    # ---- Campaigns active + leads attributed ----
    campaigns_7d = Campaign.objects.filter(
        Q(start_date__lte=today) | Q(start_date__isnull=True),
        Q(end_date__gte=today) | Q(end_date__isnull=True),
        status__in=[C.CAMPAIGN_STATUS_ACTIVE, C.CAMPAIGN_STATUS_SCHEDULED],
    )
    campaign_stats = []
    for camp in campaigns_7d[:10]:
        n_leads = Lead.objects.filter(
            created_at__gte=since,
            marketing_source__campaigns=camp,
        ).count()
        campaign_stats.append({
            'name': camp.name,
            'channel': camp.get_channel_display(),
            'status': camp.get_status_display(),
            'leads': n_leads,
            'target_leads': camp.target_leads or 0,
        })

    # ---- Top salespersons ----
    sp_stats = (
        leads_7d
        .filter(spo__isnull=False)
        .values('spo__name')
        .annotate(
            total=Count('id'),
            converted=Count('id', filter=Q(status='converted')),
            positive=Count('id', filter=Q(status='positive')),
        )
        .order_by('-converted', '-total')[:10]
    )

    # ---- Reactivation opportunities ----
    from marketing.services import reactivation as react
    dormant_count = 0
    try:
        dormant_count = len(react.reactivation_candidates(days=60))
    except Exception:
        logger.exception('weekly_report: reactivation_candidates failed')

    # ---- Referrals ----
    referrals_7d = Referral.objects.filter(created_at__gte=since)
    referrals_pending = referrals_7d.filter(status=C.REFERRAL_STATUS_PENDING).count()
    referrals_converted = referrals_7d.filter(
        status__in=[C.REFERRAL_STATUS_CONVERTED, C.REFERRAL_STATUS_REWARDED]
    ).count()

    # ---- Consent & suppression ----
    consent_granted = MarketingConsent.objects.filter(
        status=C.CONSENT_GRANTED, updated_at__gte=since
    ).count()
    consent_denied = MarketingConsent.objects.filter(
        status=C.CONSENT_DENIED, updated_at__gte=since
    ).count()
    suppressed_7d = SuppressionEntry.objects.filter(created_at__gte=since).count()

    # ---- Content published ----
    content_published = MarketingContent.objects.filter(
        published_at__gte=since
    ).count()

    return {
        'period': {
            'days': days,
            'from': since,
            'to': timezone.now(),
            'label': f'{since:%d %b} – {timezone.now():%d %b %Y}',
        },
        'summary': {
            'total_leads': total_leads,
            'hot_leads': hot_leads,
            'warm_leads': warm_leads,
            'cold_leads': cold_leads,
            'unknown_leads': unknown_leads,
            'converted_7d': converted_7d,
            'lost_7d': lost_7d,
            'revenue_7d': revenue_7d,
            'conversion_rate': round(100 * converted_7d / total_leads, 1) if total_leads else 0,
        },
        'by_source': list(by_source),
        'campaign_stats': campaign_stats,
        'sp_stats': list(sp_stats),
        'reactivation': {'dormant_count': dormant_count},
        'referrals': {
            'total': referrals_7d.count(),
            'pending': referrals_pending,
            'converted': referrals_converted,
        },
        'consent': {
            'granted': consent_granted,
            'denied': consent_denied,
            'suppressed': suppressed_7d,
        },
        'content': {'published': content_published},
        'totals_all_time': {
            'leads': Lead.objects.count(),
            'leads_30d': leads_30d.count(),
        },
    }


def render_html(data):
    return render_to_string('marketing/emails/weekly_report.html', {'r': data})


def render_text(data):
    return render_to_string('marketing/emails/weekly_report.txt', {'r': data})


def default_recipients():
    """Admins / CEOs who should get the weekly report."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    emails = list(
        User.objects.filter(is_active=True, profile__role__in=['admin', 'ceo'])
        .exclude(email='')
        .values_list('email', flat=True)
    )
    if not emails:
        fallback = getattr(settings, 'DEFAULT_FROM_EMAIL', '') or ''
        if fallback:
            emails = [fallback]
    return emails


def send(recipients=None, days=7, subject_prefix='[CRM] Weekly Marketing Report'):
    """Send the report by email. Returns (sent_count, error_or_none)."""
    data = collect(days=days)
    subject = f'{subject_prefix} — {data["period"]["label"]}'
    text_body = render_text(data)
    html_body = render_html(data)

    recipients = recipients or default_recipients()
    if not recipients:
        return 0, 'No recipients configured'

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    if not from_email:
        return 0, 'DEFAULT_FROM_EMAIL not configured'

    sent = 0
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=from_email,
            to=recipients,
        )
        msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)
        sent = len(recipients)
    except Exception as exc:
        logger.exception('Weekly report send failed')
        return 0, str(exc)[:300]

    return sent, None