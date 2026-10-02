"""Data helpers that feed the redesigned dashboards (Dashboard, CEO, Analytics, Leads)."""
import calendar
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Sum
from django.utils import timezone

from .models import (Lead, Payment, Opportunity, Meeting, ActivityLog, SalesPerson,
                     Ticket, FollowUp, LEAD_SOURCE_CHOICES)

CURRENCY = getattr(settings, 'CRM_CURRENCY', '$')
PALETTE = ['#1f6bff', '#06aed4', '#7a5af8', '#f79009', '#f04438', '#12b76a']
SOURCE_LABELS = dict(LEAD_SOURCE_CHOICES)


def _f(v):
    return float(v or 0)


def change(cur, prev):
    """Return dict(pct, up) comparing two numbers."""
    cur, prev = _f(cur), _f(prev)
    if prev == 0:
        pct = 100.0 if cur > 0 else 0.0
    else:
        pct = (cur - prev) / prev * 100
    return {'pct': round(abs(pct), 1), 'up': pct >= 0}


def window(days):
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    p_end = start - timedelta(days=1)
    p_start = p_end - timedelta(days=days - 1)
    return start, today, p_start, p_end


def _rev(qs_start, qs_end):
    return _f(Payment.objects.filter(date__date__gte=qs_start, date__date__lte=qs_end)
              .aggregate(t=Sum('amount'))['t'])


def _leads(s, e, **kw):
    return Lead.objects.filter(created_at__date__gte=s, created_at__date__lte=e, **kw).count()


def kpis_dashboard(days):
    s, e, ps, pe = window(days)
    total_all = Lead.objects.count()
    new_c, new_p = _leads(s, e, status='new'), _leads(ps, pe, status='new')
    tot_c, tot_p = _leads(s, e), _leads(ps, pe)
    won_c = Lead.objects.filter(status='converted', updated_at__date__gte=s, updated_at__date__lte=e).count()
    won_p = Lead.objects.filter(status='converted', updated_at__date__gte=ps, updated_at__date__lte=pe).count()
    rev_c, rev_p = _rev(s, e), _rev(ps, pe)
    conv = round(Lead.objects.filter(status='converted').count() / total_all * 100, 1) if total_all else 0
    conv_prev_base = Lead.objects.filter(created_at__date__lte=pe)
    pc = conv_prev_base.count()
    conv_p = round(conv_prev_base.filter(status='converted').count() / pc * 100, 1) if pc else 0
    return [
        {'label': 'Total Leads', 'value': f'{total_all:,}', 'icon': '&#128101;', 'tone': 'blue', 'ch': change(tot_c, tot_p)},
        {'label': 'New Leads', 'value': f'{new_c:,}', 'icon': '&#10024;', 'tone': 'green', 'ch': change(new_c, new_p)},
        {'label': 'Deals Won', 'value': f'{won_c:,}', 'icon': '&#127942;', 'tone': 'purple', 'ch': change(won_c, won_p)},
        {'label': 'Revenue', 'value': f'{CURRENCY}{rev_c:,.0f}', 'icon': '&#128181;', 'tone': 'orange', 'ch': change(rev_c, rev_p)},
        {'label': 'Conversion Rate', 'value': f'{conv}%', 'icon': '&#127919;', 'tone': 'green', 'ch': change(conv, conv_p)},
    ]


def source_breakdown(qs=None, top=4):
    qs = Lead.objects.all() if qs is None else qs
    rows = list(qs.values('lead_source').annotate(c=Count('id')).order_by('-c'))
    out = [{'label': SOURCE_LABELS.get(r['lead_source'], r['lead_source'] or 'Unknown'), 'value': r['c']} for r in rows[:top]]
    rest = sum(r['c'] for r in rows[top:])
    if rest:
        out.append({'label': 'Others', 'value': rest})
    for i, d in enumerate(out):
        d['color'] = ['#1f6bff', '#06aed4', '#7a5af8', '#f04438', '#f79009'][i % 5]
    return out


def opp_funnel():
    """Pipeline funnel: cumulative count of opportunities that reached each stage (lost excluded).
    Falls back to lead statuses when there are no opportunities."""
    order = ['new', 'contacted', 'qualified', 'proposal', 'negotiation', 'won']
    groups = [('Prospecting', 0), ('Qualification', 2), ('Proposal', 3), ('Negotiation', 4), ('Closed Won', 5)]
    colors = [('#2f7de0', '#1f5bd6'), ('#22b8cf', '#0f8fb0'), ('#7a5af8', '#5a3fd0'), ('#f79009', '#e3631b'), ('#12b76a', '#0b8f4f')]
    opps = Opportunity.objects.exclude(stage='lost')
    out = []
    for (label, idx), (c1, c2) in zip(groups, colors):
        q = opps.filter(stage__in=order[idx:])
        out.append({'label': label, 'value': q.count(), 'amount': _f(q.aggregate(t=Sum('value'))['t']), 'color': c1, 'color2': c2})
    if not any(o['value'] for o in out):  # fallback → leads
        L = Lead.objects
        vals = [L.count(), L.filter(status__in=['positive', 'quotation', 'converted']).count(),
                L.filter(status__in=['quotation', 'converted']).count(), L.filter(status__in=['quotation', 'converted']).count(),
                L.filter(status='converted').count()]
        for o, v in zip(out, vals):
            o['value'] = v
            o['amount'] = 0
    return out


def lead_funnel():
    L = Lead.objects
    vals = [('Total Leads', L.count()),
            ('Qualified', L.filter(status__in=['positive', 'quotation', 'converted']).count()),
            ('Proposal Sent', L.filter(status__in=['quotation', 'converted']).count()),
            ('Closed Won', L.filter(status='converted').count())]
    cols = [('#2f7de0', '#1f5bd6'), ('#22b8cf', '#0f8fb0'), ('#7a5af8', '#5a3fd0'), ('#12b76a', '#0b8f4f')]
    return [{'label': l, 'value': v, 'color': c[0], 'color2': c[1]} for (l, v), c in zip(vals, cols)]


def revenue_series():
    """daily (7d), weekly (8w), monthly (6m) payment totals for the line chart."""
    today = timezone.localdate()
    daily_l, daily_v = [], []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        daily_l.append(d.strftime('%b %d'))
        daily_v.append(_f(Payment.objects.filter(date__date=d).aggregate(t=Sum('amount'))['t']))
    weekly_l, weekly_v = [], []
    for i in range(7, -1, -1):
        e = today - timedelta(days=i * 7)
        s = e - timedelta(days=6)
        weekly_l.append(s.strftime('%b %d'))
        weekly_v.append(_rev(s, e))
    monthly_l, monthly_v = [], []
    for i in range(5, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            m += 12
            y -= 1
        monthly_l.append(f'{calendar.month_abbr[m]} {y}')
        monthly_v.append(_f(Payment.objects.filter(date__year=y, date__month=m).aggregate(t=Sum('amount'))['t']))
    return {'daily': {'labels': daily_l, 'values': daily_v},
            'weekly': {'labels': weekly_l, 'values': weekly_v},
            'monthly': {'labels': monthly_l, 'values': monthly_v}}


def monthly_performance(n=12):
    today = timezone.localdate()
    labels, vals = [], []
    for i in range(n - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            m += 12
            y -= 1
        labels.append(calendar.month_abbr[m])
        vals.append(_f(Payment.objects.filter(date__year=y, date__month=m).aggregate(t=Sum('amount'))['t']))
    target = getattr(settings, 'CRM_MONTHLY_REVENUE_TARGET', None)
    return {'labels': labels, 'values': vals, 'target': [float(target)] * n if target else None}


def deals_series(days=8):
    today = timezone.localdate()
    labels, rev, won, lost = [], [], [], []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        labels.append(d.strftime('%b %d'))
        rev.append(_f(Payment.objects.filter(date__date=d).aggregate(t=Sum('amount'))['t']))
        won.append(Lead.objects.filter(status='converted', updated_at__date=d).count())
        lost.append(Lead.objects.filter(status='lost', updated_at__date=d).count())
    return {'labels': labels, 'revenue': rev, 'won': won, 'lost': lost}


def recent_activities(n=5):
    kind = {'created': ('New Lead', 'new'), 'updated': ('Updated', 'contacted'), 'status_changed': ('Status Change', 'quotation'),
            'assigned': ('Assigned', 'contacted'), 'payment': ('Payment', 'positive'), 'converted': ('Converted', 'converted'),
            'commented': ('Note', 'contacted'), 'deleted': ('Deleted', 'lost'), 'login': ('Login', 'new')}
    rows = []
    now = timezone.now()
    for a in ActivityLog.objects.select_related('user')[:n]:
        diff = now - a.created_at
        ago = (f'{int(diff.total_seconds() // 86400)}d ago' if diff.days else
               f'{int(diff.total_seconds() // 3600)}h ago' if diff.total_seconds() >= 3600 else
               f'{max(int(diff.total_seconds() // 60), 1)}m ago')
        label, tone = kind.get(a.action, (a.get_action_display(), 'new'))
        rows.append({'label': label, 'tone': tone, 'detail': a.entity_name or a.description[:40],
                     'who': (a.user.get_full_name() or a.user.username) if a.user else 'System', 'ago': ago})
    return rows


def upcoming_meetings(n=4):
    now = timezone.now()
    out = []
    for m in Meeting.objects.filter(scheduled_at__gte=now, status='scheduled').select_related('lead', 'company').order_by('scheduled_at')[:n]:
        loc = timezone.localtime(m.scheduled_at)
        out.append({'pk': m.pk, 'time': loc.strftime('%I:%M %p'), 'title': (m.lead.name if m.lead else (m.company.name if m.company else m.title)),
                    'sub': m.title, 'today': loc.date() == timezone.localdate(), 'date': loc.strftime('%b %d')})
    return out


def regions(n=4):
    rows = list(Lead.objects.exclude(country__isnull=True).exclude(country='').values('country').annotate(c=Count('id')).order_by('-c'))
    total = Lead.objects.count() or 1
    top = rows[:n]
    out = [{'name': r['country'], 'value': r['c'], 'pct': round(r['c'] / total * 100)} for r in top]
    rest = total - sum(r['value'] for r in out)
    if rest > 0:
        out.append({'name': 'Others', 'value': rest, 'pct': round(rest / total * 100)})
    return out


def top_sales_people(n=5):
    rows = []
    for spo in SalesPerson.objects.all():
        ls = Lead.objects.filter(spo=spo)
        total, conv = ls.count(), ls.filter(status='converted').count()
        rev = _f(Payment.objects.filter(lead__spo=spo).aggregate(t=Sum('amount'))['t'])
        rows.append({'name': spo.name, 'revenue': rev, 'deals': conv, 'rate': round(conv / total * 100) if total else 0,
                     'initials': ''.join(w[0] for w in spo.name.split()[:2]).upper()})
    rows.sort(key=lambda r: (r['revenue'], r['deals']), reverse=True)
    return rows[:n]


def top_deals(n=5):
    """'Top Products / Services' → best performing opportunities grouped by title."""
    s, e, ps, pe = window(30)
    rows = []
    qs = (Opportunity.objects.filter(stage='won').values('title').annotate(t=Sum('value')).order_by('-t')[:n])
    for r in qs:
        cur = _f(Opportunity.objects.filter(stage='won', title=r['title'], updated_at__date__gte=s).aggregate(t=Sum('value'))['t'])
        prev = _f(Opportunity.objects.filter(stage='won', title=r['title'], updated_at__date__gte=ps, updated_at__date__lte=pe).aggregate(t=Sum('value'))['t'])
        rows.append({'name': r['title'], 'revenue': _f(r['t']), 'growth': change(cur, prev)})
    return rows


def kpis_analytics():
    s, e, ps, pe = window(30)
    total_all = Lead.objects.count()
    conv_all = Lead.objects.filter(status='converted').count()
    rev_all = _f(Payment.objects.aggregate(t=Sum('amount'))['t'])
    paid_leads = Payment.objects.values('lead').distinct().count()
    avg_deal = rev_all / paid_leads if paid_leads else 0
    rev_c, rev_p = _rev(s, e), _rev(ps, pe)
    conv_c = Lead.objects.filter(status='converted', updated_at__date__gte=s).count()
    conv_p = Lead.objects.filter(status='converted', updated_at__date__gte=ps, updated_at__date__lte=pe).count()
    rate = round(conv_all / total_all * 100, 1) if total_all else 0
    return [
        {'label': 'Total Leads', 'value': f'{total_all:,}', 'icon': '&#128101;', 'tone': 'blue', 'ch': change(_leads(s, e), _leads(ps, pe))},
        {'label': 'Converted Leads', 'value': f'{conv_all:,}', 'icon': '&#10003;', 'tone': 'cyan', 'ch': change(conv_c, conv_p)},
        {'label': 'Conversion Rate', 'value': f'{rate}%', 'icon': '&#127919;', 'tone': 'purple', 'ch': change(conv_c, conv_p)},
        {'label': 'Revenue Generated', 'value': f'{CURRENCY}{rev_all:,.0f}', 'icon': '&#128181;', 'tone': 'orange', 'ch': change(rev_c, rev_p)},
        {'label': 'Avg. Deal Size', 'value': f'{CURRENCY}{avg_deal:,.0f}', 'icon': '&#128200;', 'tone': 'green', 'ch': change(rev_c, rev_p)},
    ]


def lead_tiles():
    L = Lead.objects
    wk = timezone.localdate() - timedelta(days=7)
    out = []
    for key, label, icon, tone in [(None, 'Total Leads', '&#128101;', 'blue'), ('new', 'New', '&#10024;', 'cyan'),
                                   ('positive', 'Positive', '&#128077;', 'green'), ('quotation', 'Quotation', '&#128203;', 'orange'),
                                   ('converted', 'Converted', '&#10003;', 'purple'), ('lost', 'Lost', '&#10006;', 'red')]:
        q = L.filter(status=key) if key else L.all()
        out.append({'label': label, 'value': q.count(), 'icon': icon, 'tone': tone,
                    'ch': change(q.filter(created_at__date__gte=wk).count(), q.filter(created_at__date__lt=wk, created_at__date__gte=wk - timedelta(days=7)).count())})
    return out
