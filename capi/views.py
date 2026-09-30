import hmac
import json
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from leads.decorators import role_required
from leads.models import Lead

from . import constants as C
from .forms import ConfigForm, ConnectionForm, TrackingForm
from .models import (ConversionEvent, ConversionEventConfig, ConversionEventLog,
                     LeadTracking, PlatformConnection)
from .services import linkedin, meta
from .services import queue as q
from .services.intake import upsert_lead_from_platform

# Conversion API Center is restricted to admins / CEO (superusers always pass).
capi_admin = role_required('admin', 'ceo')

TABS = {
    'queue': ([C.ST_PENDING, C.ST_PROCESSING], 'Conversion Queue'),
    'sent': ([C.ST_SENT], 'Sent Events'),
    'failed': ([C.ST_FAILED, C.ST_SKIPPED], 'Failed Events'),
    'retry': ([C.ST_RETRYING], 'Retry Queue'),
}


def _conn(platform):
    if platform not in dict(C.PLATFORM_CHOICES):
        raise Http404
    obj, _ = PlatformConnection.objects.get_or_create(
        platform=platform, defaults={'name': dict(C.PLATFORM_CHOICES)[platform]})
    return obj


def _ctx(extra=None):
    ctx = {'active': 'capi'}
    ctx.update(extra or {})
    return ctx


@capi_admin
def dashboard(request):
    since = timezone.now() - timedelta(days=30)
    recent = ConversionEvent.objects.filter(created_at__gte=since)
    by_status = dict(recent.values_list('status').annotate(n=Count('id')))
    sent, failed = by_status.get(C.ST_SENT, 0), by_status.get(C.ST_FAILED, 0)

    coverage = Counter()
    sent_qs = list(ConversionEvent.objects.filter(status=C.ST_SENT).order_by('-id')[:500])
    for ev in sent_qs:
        coverage.update(ev.identifiers_used or [])

    connections = [_conn(p) for p, _ in C.PLATFORM_CHOICES]
    return render(request, 'capi/dashboard.html', _ctx({
        'connections': connections,
        'kpis': [
            ('Queued', by_status.get(C.ST_PENDING, 0) + by_status.get(C.ST_PROCESSING, 0)),
            ('Retrying', by_status.get(C.ST_RETRYING, 0)),
            ('Sent (30d)', sent),
            ('Failed (30d)', failed),
            ('Skipped (30d)', by_status.get(C.ST_SKIPPED, 0)),
            ('Success rate', f'{round(100 * sent / (sent + failed))}%' if sent + failed else '—'),
        ],
        'enabled_configs': ConversionEventConfig.objects.filter(is_enabled=True).count(),
        'total_configs': ConversionEventConfig.objects.count(),
        'by_event': recent.values('platform', 'event_name', 'status').annotate(n=Count('id')).order_by('-n')[:15],
        'by_campaign': (ConversionEvent.objects.filter(status=C.ST_SENT)
                        .exclude(campaign_name='').values('platform', 'campaign_name')
                        .annotate(n=Count('id')).order_by('-n')[:10]),
        'coverage': [(k, v, round(100 * v / len(sent_qs))) for k, v in coverage.most_common()] if sent_qs else [],
        'recent': ConversionEvent.objects.select_related('config')[:10],
    }))


@capi_admin
def connections(request):
    forms = {}
    for platform, label in C.PLATFORM_CHOICES:
        conn = _conn(platform)
        if request.method == 'POST' and request.POST.get('platform') == platform:
            form = ConnectionForm(request.POST, instance=conn, platform=platform)
            if form.is_valid():
                form.save()
                messages.success(request, f'{label} connection saved.')
                return redirect('capi:connections')
        else:
            form = ConnectionForm(instance=conn, platform=platform)
        forms[platform] = (conn, form, label)
    return render(request, 'capi/connections.html', _ctx({'forms': forms.values()}))


@capi_admin
@require_POST
def connection_test(request, platform):
    conn = _conn(platform)
    if not conn.is_configured:
        ok, msg, token_bad = False, 'Add the required IDs and an access token first.', False
    else:
        ok, msg, token_bad = (meta if platform == C.PLATFORM_META else linkedin).check_health(conn)
    conn.health_status = 'ok' if ok else 'error'
    conn.health_message = msg[:500]
    conn.last_checked_at = timezone.now()
    if ok:
        conn.token_invalid = False
    elif token_bad:
        conn.token_invalid = True
    conn.save()
    (messages.success if ok else messages.error)(request, f'{conn}: {msg}')
    return redirect('capi:connections')


@capi_admin
def config_list(request):
    configs = ConversionEventConfig.objects.select_related('connection')
    return render(request, 'capi/config_list.html', _ctx({'configs': configs}))


@capi_admin
def config_edit(request, pk=None):
    obj = get_object_or_404(ConversionEventConfig, pk=pk) if pk else None
    for p, _ in C.PLATFORM_CHOICES:
        _conn(p)  # make sure both connections exist for the dropdown
    form = ConfigForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Event mapping saved.')
        return redirect('capi:config_list')
    return render(request, 'capi/config_form.html', _ctx({'form': form, 'obj': obj}))


@capi_admin
@require_POST
def config_toggle(request, pk):
    cfg = get_object_or_404(ConversionEventConfig, pk=pk)
    cfg.is_enabled = not cfg.is_enabled
    cfg.save(update_fields=['is_enabled', 'updated_at'])
    messages.success(request, f'"{cfg.name}" {"enabled" if cfg.is_enabled else "disabled"}.')
    return redirect('capi:config_list')


@capi_admin
@require_POST
def config_associate(request, pk):
    cfg = get_object_or_404(ConversionEventConfig, pk=pk, connection__platform=C.PLATFORM_LINKEDIN)
    if not cfg.linkedin_campaign_urns.strip():
        messages.error(request, 'Add at least one campaign URN first.')
    else:
        results = linkedin.associate_campaigns(cfg.connection, cfg)
        ok = sum(1 for _, good, _ in results if good)
        (messages.success if ok == len(results) else messages.error)(
            request, f'Associated {ok}/{len(results)} campaign(s) with the conversion rule.')
    return redirect('capi:config_list')


@capi_admin
def event_list(request):
    tab = request.GET.get('tab', 'queue')
    if tab not in TABS:
        tab = 'queue'
    statuses, title = TABS[tab]
    qs = ConversionEvent.objects.filter(status__in=statuses).select_related('config')
    if request.GET.get('platform') in dict(C.PLATFORM_CHOICES):
        qs = qs.filter(platform=request.GET['platform'])
    search = request.GET.get('q', '').strip()
    if search:
        qs = qs.filter(Q(lead_label__icontains=search) | Q(event_name__icontains=search)
                       | Q(campaign_name__icontains=search) | Q(event_id__icontains=search))
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    return render(request, 'capi/event_list.html', _ctx({
        'page_obj': page, 'tab': tab, 'title': title, 'tabs': TABS, 'search': search,
        'platform': request.GET.get('platform', '')}))


@capi_admin
def event_detail(request, pk):
    ev = get_object_or_404(ConversionEvent.objects.select_related('config__connection', 'lead'), pk=pk)
    return render(request, 'capi/event_detail.html', _ctx({'ev': ev, 'logs': ev.logs.all()}))


@capi_admin
@require_POST
def event_action(request, pk, action):
    ev = get_object_or_404(ConversionEvent, pk=pk)
    if action in ('retry', 'send-now'):
        done = q.retry_now(ev)
        messages.info(request, f'Event is now "{done.get_status_display()}".')
    elif action == 'cancel' and ev.status in (C.ST_PENDING, C.ST_RETRYING, C.ST_FAILED, C.ST_SKIPPED):
        ev.status = C.ST_CANCELLED
        ev.save(update_fields=['status'])
        messages.success(request, 'Event cancelled - it will not be sent.')
    else:
        raise Http404
    return redirect('capi:event_detail', pk=pk)


@capi_admin
@require_POST
def process_now(request):
    counts = q.process_queue()
    messages.success(request, 'Queue processed: ' + ', '.join(f'{v} {k}' for k, v in counts.items() if v)
                     if any(counts.values()) else 'Nothing due in the queue.')
    return redirect(request.META.get('HTTP_REFERER') or 'capi:dashboard')


@capi_admin
def log_list(request):
    qs = ConversionEventLog.objects.select_related('event')
    if request.GET.get('ok') in ('0', '1'):
        qs = qs.filter(success=request.GET['ok'] == '1')
    page = Paginator(qs, 40).get_page(request.GET.get('page'))
    return render(request, 'capi/log_list.html', _ctx({'page_obj': page, 'ok': request.GET.get('ok', '')}))


@capi_admin
def lead_tracking(request, lead_id):
    lead = get_object_or_404(Lead, pk=lead_id)
    tracking, _ = LeadTracking.objects.get_or_create(lead=lead)
    form = TrackingForm(request.POST or None, instance=tracking)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Attribution & consent saved.')
        return redirect('lead_detail', pk=lead.pk)
    return render(request, 'capi/tracking_form.html', _ctx({
        'form': form, 'lead': lead, 'touchpoints': lead.capi_touchpoints.all()[:20]}))


@csrf_exempt
@require_POST
def intake(request, platform):
    """Lead direction: Lead Sync / Zapier / Make -> CRM. Protected by a shared secret."""
    secret = getattr(settings, 'CAPI_INTAKE_SECRET', '')
    if not secret:
        raise Http404
    if platform not in ('meta', 'linkedin'):
        raise Http404
    if not hmac.compare_digest(request.headers.get('X-CAPI-Intake-Key', ''), secret):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'invalid json'}, status=400)
    if not (data.get('email') or data.get('phone')):
        return JsonResponse({'error': 'email or phone required'}, status=400)
    consent = {True: C.CONSENT_GRANTED, False: C.CONSENT_DENIED}.get(data.get('consent'))
    fields = {k: data.get(k) for k in ('campaign_id', 'campaign_name', 'adset_id', 'ad_id', 'form_id',
                                       'external_lead_id', 'fbc', 'fbp', 'li_fat_id')}
    lead, created = upsert_lead_from_platform(
        platform, name=data.get('name', ''), email=data.get('email', ''), phone=data.get('phone', ''),
        company=data.get('company', ''), consent=consent, **fields)
    return JsonResponse({'lead_id': lead.pk, 'created': created}, status=201 if created else 200)
