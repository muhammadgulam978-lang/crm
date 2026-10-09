import hmac
import json
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.views.decorators.http import require_POST, require_http_methods

from leads.decorators import role_required
from leads.models import Lead

from . import conf
from . import constants as C
from .forms import ConfigForm, ConnectionForm, TrackingForm



from .models import (ConversionEvent, ConversionEventConfig, ConversionEventLog,
                     LeadTracking, PlatformConnection, MetaWebhookEvent,
                     LinkedInWebhookEvent)
from .services import linkedin, meta
from .services import queue as q
from .services import meta_lead_ads as mla
from .services import meta_webhook as mwh
from .services import linkedin_lead_ads as lla
from .services import linkedin_webhook as lwh
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

    return render(request, 'capi/connections.html', _ctx({
        'forms': forms.values(),
        'meta': meta_status(request),
    }))


def meta_status(request):
    """Everything the admin needs to see to know whether Lead Ads are flowing."""
    conn = _conn(C.PLATFORM_META)
    events = MetaWebhookEvent.objects
    since = timezone.now() - timedelta(days=30)
    recent = events.filter(created_at__gte=since)
    last = events.order_by('-created_at').first()
    verify_set = bool(conn.webhook_verify_token or conn_env_verify())
    checks = [
        ('Meta App Secret (META_APP_SECRET)', bool(conf.get('META_APP_SECRET')),
         'Needed to verify X-Hub-Signature-256 on every webhook POST.'),
        ('Facebook Page ID', bool(conn.page_id), ''),
        ('Page Access Token', bool(conn.get_page_token()), 'Needed to fetch lead details.'),
        ('Webhook verify token', verify_set, 'Used for the one-time GET handshake.'),
        ('Webhook verified by Meta', bool(conn.webhook_verified_at), ''),
        ('Page subscribed to leadgen', bool(conn.webhook_subscribed_at), ''),
        ('Conversions API token + dataset', conn.is_configured, 'For sending events back (CAPI).'),
    ]
    return {
        'conn': conn,
        'webhook_url': settings_webhook_url(request),
        'checks': checks,
        'ready': all(ok for _, ok, _ in checks[:4]),
        'counts': {
            'received': recent.count(),
            'imported': recent.filter(processed=True).count(),
            'retrying': events.filter(processed=False, gave_up=False).count(),
            'gave_up': events.filter(processed=False, gave_up=True).count(),
        },
        'last_event': last,
        'recent': events.select_related('lead')[:10],
    }


def conn_env_verify():
    return conf.get('META_WEBHOOK_VERIFY_TOKEN')


def settings_webhook_url(request):
    """Callback URL to paste into Meta. Set CAPI_PUBLIC_BASE_URL (https://crm.example.com)
    when running behind a proxy that hides the public scheme/host."""
    base = conf.get('CAPI_PUBLIC_BASE_URL')
    path = reverse('capi:meta_webhook')
    return f'{base.rstrip("/")}{path}' if base else request.build_absolute_uri(path)


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
    leads = mwh.retry_due()
    if leads['processed']:
        counts['leads imported'] = leads['processed']
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
    fields = {k: data.get(k) for k in ('campaign_id', 'campaign_name', 'adset_id', 'adset_name', 'ad_id',
                                       'ad_name', 'page_id', 'form_id', 'external_lead_id',
                                       'fbc', 'fbp', 'li_fat_id')}
    answers = data.get('form_answers')
    fields['form_answers'] = answers if isinstance(answers, dict) else {}
    lead, created = upsert_lead_from_platform(
        platform, name=data.get('name', ''), email=data.get('email', ''), phone=data.get('phone', ''),
        company=data.get('company', ''), consent=consent, **fields)
    return JsonResponse({'lead_id': lead.pk, 'created': created}, status=201 if created else 200)

###########################################
############################################
############################################

from .models import (ConversionEvent, ConversionEventConfig, ConversionEventLog,
                     LeadTracking, PlatformConnection, MetaWebhookEvent,
                     LinkedInWebhookEvent)
from .services import linkedin_lead_ads as lla
from .services import linkedin_webhook as lwh

@csrf_exempt
@require_http_methods(['GET', 'POST'])
def meta_webhook(request):
    """GET: Meta verification handshake. POST: signed Meta Lead Ads notification."""
    if request.method == 'GET':
        mode = request.GET.get('hub.mode', '')
        token = request.GET.get('hub.verify_token', '')
        challenge = request.GET.get('hub.challenge', '')
        conn = PlatformConnection.objects.filter(platform=C.PLATFORM_META).first()
        for expected in (conn.webhook_verify_token if conn else '', conn_env_verify()):
            result = mla.check_handshake(mode, token, challenge, expected)
            if result is not None:
                if conn:
                    PlatformConnection.objects.filter(pk=conn.pk).update(webhook_verified_at=timezone.now())
                return HttpResponse(result, content_type='text/plain')
        return HttpResponse('forbidden', status=403)

    outcome = mwh.process_payload(request.body, request.headers.get('X-Hub-Signature-256', ''))
    if not outcome['ok']:
        return JsonResponse({'ok': False, 'errors': outcome['errors']}, status=401)
    return JsonResponse(outcome, status=200)


@capi_admin
@require_POST
def meta_subscribe_page(request):
    """Ask Meta to start sending leadgen events for the configured page, then confirm it."""
    conn = _conn(C.PLATFORM_META)
    token = conn.get_page_token()
    if not (conn.page_id and token):
        messages.error(request, 'Set the Page ID and Page Access Token first.')
        return redirect('capi:connections')
    version = conn.effective_api_version
    ok, msg = mla.subscribe_page(conn.page_id, token, api_version=version)
    if not ok:
        messages.error(request, f'Subscription failed: {msg}')
        return redirect('capi:connections')
    good, subs = mla.list_subscriptions(conn.page_id, token, api_version=version)
    if good and not mla.page_has_leadgen_subscription(subs):
        messages.error(request, 'Meta accepted the call but the Page is not subscribed to "leadgen". '
                                'Check the app has leads_retrieval + pages_manage_metadata.')
        return redirect('capi:connections')
    conn.webhook_subscribed_at = timezone.now()
    conn.save(update_fields=['webhook_subscribed_at'])
    messages.success(request, 'Page subscribed to leadgen webhooks.')
    return redirect('capi:connections')


@capi_admin
@require_POST
def meta_page_test(request):
    """Validate the Page token and the leadgen subscription without importing anything."""
    conn = _conn(C.PLATFORM_META)
    token = conn.get_page_token()
    if not (conn.page_id and token):
        messages.error(request, 'Set the Page ID and Page Access Token first.')
        return redirect('capi:connections')
    ok, msg = mla.check_page(conn.page_id, token, api_version=conn.effective_api_version)
    if ok:
        good, subs = mla.list_subscriptions(conn.page_id, token, api_version=conn.effective_api_version)
        subscribed = good and mla.page_has_leadgen_subscription(subs)
        msg += ' Subscribed to leadgen.' if subscribed else ' NOT subscribed to leadgen yet.'
        ok = subscribed
    (messages.success if ok else messages.error)(request, f'Facebook Page: {msg}')
    return redirect('capi:connections')


@capi_admin
def meta_event_list(request):
    state = request.GET.get('state', '')
    qs = MetaWebhookEvent.objects.select_related('lead')
    if state == 'imported':
        qs = qs.filter(processed=True)
    elif state == 'retrying':
        qs = qs.filter(processed=False, gave_up=False)
    elif state == 'failed':
        qs = qs.filter(processed=False, gave_up=True)
    search = request.GET.get('q', '').strip()
    if search:
        qs = qs.filter(Q(leadgen_id__icontains=search) | Q(campaign_id__icontains=search)
                       | Q(form_id__icontains=search) | Q(lead__name__icontains=search))
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    return render(request, 'capi/meta_events.html', _ctx({'page_obj': page, 'state': state, 'search': search}))


@capi_admin
@require_POST
def meta_event_retry(request, pk):
    ev = get_object_or_404(MetaWebhookEvent, pk=pk)
    outcome = mwh.reset_and_process(ev)
    ev.refresh_from_db()
    if outcome == 'processed':
        messages.success(request, f'Lead {ev.leadgen_id} imported.')
    elif outcome == 'skipped':
        messages.info(request, 'Already imported.')
    else:
        messages.error(request, f'Still failing: {ev.error or outcome}')
    return redirect(request.META.get('HTTP_REFERER') or 'capi:meta_event_list')


@capi_admin
@require_POST
def meta_retry_all(request):
    counts = mwh.retry_due()
    messages.success(request, 'Lead Ads retry: ' + ', '.join(f'{v} {k}' for k, v in counts.items() if v)
                     if any(counts.values()) else 'Nothing due for retry.')
    return redirect('capi:meta_event_list')


# --------------------------------------------------------------- LinkedIn webhook
@csrf_exempt
@require_http_methods(['GET', 'POST'])
def linkedin_webhook(request):
    """
    GET  -> acknowledge (LinkedIn may probe the URL).
    POST -> challenge handshake OR lead notification(s).
    """
    if request.method == 'GET':
        return HttpResponse('ok', content_type='text/plain')

    raw_body = request.body
    try:
        body = json.loads(raw_body or b'{}')
    except ValueError:
        return JsonResponse({'error': 'invalid json'}, status=400)

    # 1) Challenge verification
    is_challenge, response = lwh.handle_challenge(body)
    if is_challenge:
        return JsonResponse(response, status=200)

    # 2) Optional signature check (LinkedIn doesn't sign by default)
    signature = request.headers.get('X-LI-Signature', '')
    secret = (getattr(settings, 'CAPI_LINKEDIN_CLIENT_SECRET', '')
              or getattr(settings, 'LINKEDIN_CLIENT_SECRET', ''))
    if signature and not lla.verify_webhook_payload(raw_body, signature, secret):
        return JsonResponse({'error': 'signature_mismatch'}, status=401)

    outcome = lwh.process_payload(raw_body)
    return JsonResponse(outcome, status=200 if outcome['ok'] else 400)


# --------------------------------------------------------------- LinkedIn Lead Sync test
@capi_admin
@require_POST
def linkedin_leadsync_test(request):
    conn = _conn(C.PLATFORM_LINKEDIN)
    if not conn.is_configured or not conn.linkedin_organization_urn:
        ok, msg, token_bad = False, 'Set the access token and Organization URN first.', False
    else:
        ok, msg, token_bad = lla.check_health(conn)
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
