"""Marketing Center views — Phase 1."""
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

import csv
from django.http import HttpResponse
from django.core.paginator import Paginator
from django.db.models import Q

from .forms import ProspectForm, ProspectImportForm
from .services import prospect_import as pi
from django.views.decorators.http import require_POST


from leads.decorators import role_required
from leads.models import Lead
from .models import (ProspectSource, Campaign, MarketingContent, MarketingConsent,
                     SuppressionEntry, AutomationRun, AutomationStep, Referral)
from . import constants as C

# Marketing Center — admin / manager / CEO only (superuser auto-passes)
marketing_access = role_required('admin', 'sales_manager', 'ceo')

@marketing_access
def dashboard(request):
    from marketing.services import analytics

    days = int(request.GET.get('days', 30))
    if days not in (7, 14, 30, 90):
        days = 30

    kpi_data = analytics.kpis(days=days)
    kpi_data['revenue_by_source'] = analytics.revenue_by_source(days=days)

    data = {
        'active': 'marketing',
        'days': days,
        'kpis': kpi_data,
        'sources': analytics.source_performance(days=days),
        'funnel': analytics.conversion_funnel(days=days),
        'lost_reasons': analytics.lost_reasons(days=days * 3),
        'content': analytics.content_performance(days=days),
        'hot_leads': analytics.hot_leads(days=days),
        'reactivation': analytics.reactivation_table(days=60),
        'recent_campaigns': Campaign.objects.order_by('-created_at')[:5],
        'recent_drafts': analytics.recent_drafts(limit=10),
    }
    return render(request, 'marketing/dashboard.html', data)

    # Lead funnel by marketing_source (uses Lead.marketing_source if you added it)
    has_mkt_source = hasattr(Lead, 'marketing_source')
    kpis = [
        ('Campaigns', campaigns.count()),
        ('Active now', campaigns.filter(status=C.CAMPAIGN_STATUS_ACTIVE).count()),
        ('Sources', ProspectSource.objects.filter(is_active=True).count()),
        ('Content pieces', MarketingContent.objects.filter(is_active=True).count()),
        ('Consent granted', MarketingConsent.objects.filter(status=C.CONSENT_GRANTED).count()),
        ('Suppressed', SuppressionEntry.objects.count()),
        ('Referrals', Referral.objects.count()),
        ('Automations', AutomationRun.objects.exclude(status='done').count()),
    ]

    ctx = {
        'active': 'marketing',
        'kpis': kpis,
        'sources': sources_with_leads,
        'by_channel': by_channel,
        'recent_campaigns': campaigns.order_by('-created_at')[:8],
        'pending_referrals': Referral.objects.filter(status=C.REFERRAL_STATUS_PENDING).count(),
        'pending_automations': AutomationRun.objects.filter(status=C.AUTOMATION_STATUS_PENDING).count(),
        'has_mkt_source': has_mkt_source,
        'total_leads': Lead.objects.count(),
    }
    return render(request, 'marketing/dashboard.html', ctx)


@marketing_access
def campaign_list(request):
    qs = Campaign.objects.select_related('source', 'owner')
    status_filter = request.GET.get('status', '')
    if status_filter in dict(C.CAMPAIGN_STATUS_CHOICES):
        qs = qs.filter(status=status_filter)
    return render(request, 'marketing/campaign_list.html', {
        'active': 'marketing',
        'campaigns': qs,
        'status_filter': status_filter,
        'status_choices': C.CAMPAIGN_STATUS_CHOICES,
    })


@marketing_access
def source_list(request):
    qs = ProspectSource.objects.all()
    kind = request.GET.get('kind', '')
    if kind in dict(C.SOURCE_KIND_CHOICES):
        qs = qs.filter(kind=kind)
    qs = qs.annotate(lead_count=Count('leads'))
    return render(request, 'marketing/source_list.html', {
        'active': 'marketing',
        'sources': qs,
        'kind_filter': kind,
        'kind_choices': C.SOURCE_KIND_CHOICES,
    })


@marketing_access
def content_list(request):
    qs = MarketingContent.objects.select_related('campaign')
    kind = request.GET.get('kind', '')
    if kind in dict(C.CONTENT_KIND_CHOICES):
        qs = qs.filter(kind=kind)
    return render(request, 'marketing/content_list.html', {
        'active': 'marketing',
        'contents': qs[:200],
        'kind_filter': kind,
        'kind_choices': C.CONTENT_KIND_CHOICES,
    })


@marketing_access
def consent_list(request):
    qs = MarketingConsent.objects.select_related('lead')
    status_filter = request.GET.get('status', '')
    if status_filter in dict(C.CONSENT_STATUS_CHOICES):
        qs = qs.filter(status=status_filter)
    return render(request, 'marketing/consent_list.html', {
        'active': 'marketing',
        'consents': qs[:200],
        'status_filter': status_filter,
        'status_choices': C.CONSENT_STATUS_CHOICES,
    })


@marketing_access
def suppression_list(request):
    qs = SuppressionEntry.objects.all().order_by('-created_at')
    channel = request.GET.get('channel', '')
    if channel in dict(C.CHANNEL_CHOICES):
        qs = qs.filter(channel=channel)
    return render(request, 'marketing/suppression_list.html', {
        'active': 'marketing',
        'entries': qs[:200],
        'channel_filter': channel,
        'channel_choices': C.CHANNEL_CHOICES,
    })


@marketing_access
def referral_list(request):
    qs = Referral.objects.select_related('referrer_lead', 'referred_lead', 'campaign')
    status_filter = request.GET.get('status', '')
    if status_filter in dict(C.REFERRAL_STATUS_CHOICES):
        qs = qs.filter(status=status_filter)
    return render(request, 'marketing/referral_list.html', {
        'active': 'marketing',
        'referrals': qs[:200],
        'status_filter': status_filter,
        'status_choices': C.REFERRAL_STATUS_CHOICES,
    })


@marketing_access
def automation_list(request):
    qs = AutomationRun.objects.select_related('lead', 'campaign')
    status_filter = request.GET.get('status', '')
    if status_filter in dict(C.AUTOMATION_STATUS_CHOICES):
        qs = qs.filter(status=status_filter)
    qs = qs.annotate(step_count=Count('steps'))
    return render(request, 'marketing/automation_list.html', {
        'active': 'marketing',
        'runs': qs[:200],
        'status_filter': status_filter,
        'status_choices': C.AUTOMATION_STATUS_CHOICES,
    })
    
    
    
# =========================================================================
# CRUD views — Marketing Center ka apna UI (Django Admin ka istemal nahi)
# =========================================================================
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.db import transaction

from .forms import (ProspectSourceForm, CampaignForm, MarketingContentForm,
                    MarketingConsentForm, SuppressionEntryForm, ReferralForm,
                    AutomationRunForm, AutomationStepForm)
from .models import AutomationStep


# ---------- helpers ----------
def _save_form(request, form, success_url, success_msg, cancel_url, template, extra=None):
    """Generic pattern for create/edit."""
    if request.method == 'POST':
        if form.is_valid():
            obj = form.save()
            messages.success(request, success_msg)
            return redirect(success_url)
    ctx = {'form': form, 'cancel_url': cancel_url}
    if extra:
        ctx.update(extra)
    return render(request, template, ctx)


# ---------- ProspectSource ----------
@marketing_access
def source_create(request):
    form = ProspectSourceForm(request.POST or None)
    return _save_form(request, form, 'marketing:source_list',
                      'Source created.', 'marketing:source_list',
                      'marketing/source_form.html',
                      {'obj': None})


@marketing_access
def source_edit(request, pk):
    obj = get_object_or_404(ProspectSource, pk=pk)
    form = ProspectSourceForm(request.POST or None, instance=obj)
    return _save_form(request, form, 'marketing:source_list',
                      'Source updated.', 'marketing:source_list',
                      'marketing/source_form.html',
                      {'obj': obj})


@marketing_access
def source_delete(request, pk):
    obj = get_object_or_404(ProspectSource, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Source deleted.')
        return redirect('marketing:source_list')
    return render(request, 'marketing/_confirm_delete.html', {
        'active': 'marketing', 'obj': obj, 'obj_name': obj.name,
        'cancel_url': 'marketing:source_list',
    })


# ---------- Campaign ----------
@marketing_access
def campaign_create(request):
    form = CampaignForm(request.POST or None)
    return _save_form(request, form, 'marketing:campaign_list',
                      'Campaign created.', 'marketing:campaign_list',
                      'marketing/campaign_form.html',
                      {'obj': None})


@marketing_access
def campaign_edit(request, pk):
    obj = get_object_or_404(Campaign, pk=pk)
    form = CampaignForm(request.POST or None, instance=obj)
    return _save_form(request, form, 'marketing:campaign_list',
                      'Campaign updated.', 'marketing:campaign_list',
                      'marketing/campaign_form.html',
                      {'obj': obj})


@marketing_access
def campaign_delete(request, pk):
    obj = get_object_or_404(Campaign, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Campaign deleted.')
        return redirect('marketing:campaign_list')
    return render(request, 'marketing/_confirm_delete.html', {
        'active': 'marketing', 'obj': obj, 'obj_name': obj.name,
        'cancel_url': 'marketing:campaign_list',
    })


# ---------- MarketingContent ----------
@marketing_access
def content_create(request):
    form = MarketingContentForm(request.POST or None)
    return _save_form(request, form, 'marketing:content_list',
                      'Content created.', 'marketing:content_list',
                      'marketing/content_form.html',
                      {'obj': None})


@marketing_access
def content_edit(request, pk):
    obj = get_object_or_404(MarketingContent, pk=pk)
    form = MarketingContentForm(request.POST or None, instance=obj)
    return _save_form(request, form, 'marketing:content_list',
                      'Content updated.', 'marketing:content_list',
                      'marketing/content_form.html',
                      {'obj': obj})


@marketing_access
def content_delete(request, pk):
    obj = get_object_or_404(MarketingContent, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Content deleted.')
        return redirect('marketing:content_list')
    return render(request, 'marketing/_confirm_delete.html', {
        'active': 'marketing', 'obj': obj, 'obj_name': obj.title,
        'cancel_url': 'marketing:content_list',
    })


# ---------- MarketingConsent ----------
@marketing_access
def consent_create(request):
    form = MarketingConsentForm(request.POST or None)
    return _save_form(request, form, 'marketing:consent_list',
                      'Consent recorded.', 'marketing:consent_list',
                      'marketing/consent_form.html',
                      {'obj': None})


@marketing_access
def consent_edit(request, pk):
    obj = get_object_or_404(MarketingConsent, pk=pk)
    form = MarketingConsentForm(request.POST or None, instance=obj)
    return _save_form(request, form, 'marketing:consent_list',
                      'Consent updated.', 'marketing:consent_list',
                      'marketing/consent_form.html',
                      {'obj': obj})


@marketing_access
def consent_delete(request, pk):
    obj = get_object_or_404(MarketingConsent, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Consent record deleted.')
        return redirect('marketing:consent_list')
    return render(request, 'marketing/_confirm_delete.html', {
        'active': 'marketing', 'obj': obj,
        'obj_name': f'Consent {obj.channel}/{obj.purpose} for lead {obj.lead_id}',
        'cancel_url': 'marketing:consent_list',
    })


# ---------- SuppressionEntry ----------
@marketing_access
def suppression_create(request):
    form = SuppressionEntryForm(request.POST or None)
    return _save_form(request, form, 'marketing:suppression_list',
                      'Suppression added.', 'marketing:suppression_list',
                      'marketing/suppression_form.html',
                      {'obj': None})


@marketing_access
def suppression_edit(request, pk):
    obj = get_object_or_404(SuppressionEntry, pk=pk)
    form = SuppressionEntryForm(request.POST or None, instance=obj)
    return _save_form(request, form, 'marketing:suppression_list',
                      'Suppression updated.', 'marketing:suppression_list',
                      'marketing/suppression_form.html',
                      {'obj': obj})


@marketing_access
def suppression_delete(request, pk):
    obj = get_object_or_404(SuppressionEntry, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Suppression removed.')
        return redirect('marketing:suppression_list')
    return render(request, 'marketing/_confirm_delete.html', {
        'active': 'marketing', 'obj': obj, 'obj_name': f'{obj.channel}:{obj.value}',
        'cancel_url': 'marketing:suppression_list',
    })


# ---------- Referral ----------
@marketing_access
def referral_create(request):
    form = ReferralForm(request.POST or None)
    return _save_form(request, form, 'marketing:referral_list',
                      'Referral created.', 'marketing:referral_list',
                      'marketing/referral_form.html',
                      {'obj': None})


@marketing_access
def referral_edit(request, pk):
    obj = get_object_or_404(Referral, pk=pk)
    form = ReferralForm(request.POST or None, instance=obj)
    return _save_form(request, form, 'marketing:referral_list',
                      'Referral updated.', 'marketing:referral_list',
                      'marketing/referral_form.html',
                      {'obj': obj})


@marketing_access
def referral_delete(request, pk):
    obj = get_object_or_404(Referral, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Referral deleted.')
        return redirect('marketing:referral_list')
    return render(request, 'marketing/_confirm_delete.html', {
        'active': 'marketing', 'obj': obj,
        'obj_name': f'Referral #{obj.pk}', 'cancel_url': 'marketing:referral_list',
    })


# ---------- AutomationRun ----------
@marketing_access
def automation_create(request):
    form = AutomationRunForm(request.POST or None)
    return _save_form(request, form, 'marketing:automation_list',
                      'Automation run created.', 'marketing:automation_list',
                      'marketing/automation_form.html',
                      {'obj': None})


@marketing_access
def automation_edit(request, pk):
    obj = get_object_or_404(AutomationRun, pk=pk)
    form = AutomationRunForm(request.POST or None, instance=obj)
    return _save_form(request, form, 'marketing:automation_list',
                      'Automation run updated.', 'marketing:automation_list',
                      'marketing/automation_form.html',
                      {'obj': obj})


@marketing_access
def automation_delete(request, pk):
    obj = get_object_or_404(AutomationRun, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Automation run deleted.')
        return redirect('marketing:automation_list')
    return render(request, 'marketing/_confirm_delete.html', {
        'active': 'marketing', 'obj': obj,
        'obj_name': f'Automation run #{obj.pk}', 'cancel_url': 'marketing:automation_list',
    })
    
    
# =========================================================================
# Prospects — manual + CSV import
# =========================================================================





def _prospects_base_qs(request):
    """Common filters for the prospect list."""
    qs = Lead.objects.filter(marketing_source__isnull=False).select_related(
        'marketing_source', 'interested_service', 'spo'
    )
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(email__icontains=q) |
            Q(contact_number__icontains=q) | Q(business_requirement__icontains=q)
        )
    source_id = request.GET.get('source', '')
    if source_id.isdigit():
        qs = qs.filter(marketing_source_id=int(source_id))
    status = request.GET.get('status', '')
    if status:
        qs = qs.filter(status=status)
    return qs


@marketing_access
def prospect_list(request):
    qs = _prospects_base_qs(request).order_by('-created_at')
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    return render(request, 'marketing/prospect_list.html', {
        'active': 'marketing',
        'page_obj': page,
        'total': qs.count(),
        'sources': ProspectSource.objects.filter(is_active=True).order_by('kind', 'name'),
        'status_choices': [('new', 'New'), ('positive', 'Positive'),
                           ('quotation', 'Quotation'), ('converted', 'Converted'),
                           ('lost', 'Lost')],
        'q': request.GET.get('q', ''),
        'source_filter': request.GET.get('source', ''),
        'status_filter': request.GET.get('status', ''),
    })


@marketing_access
def prospect_create(request):
    if request.method == 'POST':
        form = ProspectForm(request.POST)
        if form.is_valid():
            lead = form.save(commit=False)
            if not lead.research_date:
                lead.research_date = timezone.localdate()
            lead.save()
            messages.success(request, f'Prospect "{lead.name}" created.')
            return redirect('marketing:prospect_list')
    else:
        form = ProspectForm(initial={'research_date': timezone.localdate()})
    return render(request, 'marketing/prospect_form.html', {
        'active': 'marketing', 'form': form, 'obj': None,
        'cancel_url': 'marketing:prospect_list',
    })


@marketing_access
def prospect_edit(request, pk):
    obj = get_object_or_404(Lead, pk=pk)
    if request.method == 'POST':
        form = ProspectForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, f'Prospect "{obj.name}" updated.')
            return redirect('marketing:prospect_list')
    else:
        form = ProspectForm(instance=obj)
    return render(request, 'marketing/prospect_form.html', {
        'active': 'marketing', 'form': form, 'obj': obj,
        'cancel_url': 'marketing:prospect_list',
    })


@marketing_access
def prospect_delete(request, pk):
    obj = get_object_or_404(Lead, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Prospect deleted.')
        return redirect('marketing:prospect_list')
    return render(request, 'marketing/_confirm_delete.html', {
        'active': 'marketing', 'obj': obj, 'obj_name': obj.name,
        'cancel_url': 'marketing:prospect_list',
    })


@marketing_access
def prospect_import(request):
    """Step 1 — upload."""
    if request.method == 'POST':
        form = ProspectImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                headers, rows = pi.parse_file(form.cleaned_data['file'])
            except ValueError as e:
                messages.error(request, str(e))
                return render(request, 'marketing/prospect_import.html', {
                    'active': 'marketing', 'form': form, 'step': 1,
                })
            if not rows:
                messages.error(request, 'The file contains no data rows.')
                return render(request, 'marketing/prospect_import.html', {
                    'active': 'marketing', 'form': form, 'step': 1,
                })
            validated = pi.validate_rows(rows)
            # Stash in session so we don't re-upload
            request.session['prospect_import'] = {
                'rows': [{'raw': v.raw, 'cleaned': v.cleaned, 'status': v.status,
                          'issues': v.issues, 'duplicate_of': v.duplicate_of,
                          'row_number': v.row_number} for v in validated],
                'source_id': form.cleaned_data['source'].pk if form.cleaned_data['source'] else None,
                'default_status': form.cleaned_data['default_status'],
            }
            return redirect('marketing:prospect_import_preview')
    else:
        form = ProspectImportForm()
    return render(request, 'marketing/prospect_import.html', {
        'active': 'marketing', 'form': form, 'step': 1,
    })


@marketing_access
def prospect_import_preview(request):
    """Step 2 — preview validated rows."""
    stash = request.session.get('prospect_import')
    if not stash:
        messages.error(request, 'No file uploaded. Please start again.')
        return redirect('marketing:prospect_import')

    rows = stash['rows']
    counts = {'ok': 0, 'duplicate': 0, 'invalid': 0}
    for r in rows:
        counts[r['status']] = counts.get(r['status'], 0) + 1

    if request.method == 'POST':
        # User confirmed — import
        source = None
        if stash.get('source_id'):
            source = ProspectSource.objects.filter(pk=stash['source_id']).first()
        # Re-hydrate ValidatedRow objects
        vs = []
        for r in rows:
            v = pi.ValidatedRow(
                row_number=r['row_number'], raw=r['raw'], cleaned=r['cleaned'],
                status=r['status'], issues=r['issues'], duplicate_of=r['duplicate_of'],
            )
            vs.append(v)
        result = pi.import_rows(vs, source=source,
                                 default_status=stash['default_status'],
                                 user=request.user)
        request.session.pop('prospect_import', None)
        messages.success(
            request,
            f"Import complete: {result['created']} created, "
            f"{result['skipped_duplicate']} duplicates skipped, "
            f"{result['skipped_invalid']} invalid skipped."
        )
        return redirect('marketing:prospect_list')

    return render(request, 'marketing/prospect_import_preview.html', {
        'active': 'marketing', 'rows': rows, 'counts': counts,
    })


@marketing_access
def prospect_import_template(request):
    """Download a starter CSV."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="prospect_template.csv"'
    w = csv.writer(response)
    w.writerow(['Name', 'Company', 'Email', 'Phone', 'Country', 'City',
                'Industry', 'Website', 'Source URL', 'Requirement', 'Notes'])
    w.writerow(['John Doe', 'Acme Software', 'john@acme.com', '+92 300 1234567',
                'Pakistan', 'Lahore', 'Technology', 'https://acme.com',
                'https://linkedin.com/in/johndoe',
                'Needs an AI chatbot for customer support',
                'Referred by existing client'])
    return response    


# =========================================================================
# Phase 2.5 — Reactivation Center
# =========================================================================
from .services import reactivation as react
from .services import eligibility as elig


@marketing_access
def reactivation_list(request):
    days = int(request.GET.get('days', 60))
    candidates = react.reactivation_candidates(days=days)

    rows = []
    for lead in candidates:
        last = react.last_communication(lead)
        action, reason = react.recommended_next_action(lead)
        elig_rows = elig.eligibility_summary(lead)
        rows.append({
            'lead': lead,
            'last_at': last[1] if last else None,
            'last_kind': last[0] if last else None,
            'action': action,
            'reason': reason,
            'eligibility': elig_rows,
        })

    return render(request, 'marketing/reactivation.html', {
        'active': 'marketing',
        'rows': rows,
        'days': days,
    })
    

# =========================================================================
# Phase 2.6 — Content workflow + calendar
# =========================================================================
@marketing_access
@require_POST
def content_transition(request, pk, new_status):
    obj = get_object_or_404(MarketingContent, pk=pk)
    valid = dict(MarketingContent.STATUS_CHOICES)
    if new_status not in valid:
        raise Http404
    obj.status = new_status
    if new_status == 'approved':
        obj.approved_by = request.user
        obj.approved_at = timezone.now()
    if new_status == 'published' and not obj.published_at:
        obj.published_at = timezone.now()
    obj.save()
    messages.success(request, f'Content moved to "{valid[new_status]}".')
    return redirect('marketing:content_list')


@marketing_access
def content_calendar(request):
    """Calendar view — content grouped by planned_date + published."""
    from collections import defaultdict
    from calendar import monthcalendar
    from datetime import date

    today = timezone.localdate()
    try:
        year = int(request.GET.get('year', today.year))
        month = int(request.GET.get('month', today.month))
    except ValueError:
        year, month = today.year, today.month

    first_day = date(year, month, 1)
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)

    items = MarketingContent.objects.filter(
        planned_date__gte=first_day, planned_date__lte=last_day
    ).select_related('campaign').order_by('planned_date', 'kind')

    by_day = defaultdict(list)
    for it in items:
        by_day[it.planned_date.day].append(it)

    # grid: list of weeks, each week is list of day numbers (0 for out-of-month)
    grid = monthcalendar(year, month)

    return render(request, 'marketing/content_calendar.html', {
        'active': 'marketing',
        'grid': grid,
        'by_day': dict(by_day),
        'year': year,
        'month': month,
        'month_name': first_day.strftime('%B'),
        'today': today,
        'status_choices': MarketingContent.STATUS_CHOICES,
    })
    
    
    
# =========================================================================
# Public website lead form — no auth required
# =========================================================================
import json as _json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .forms import PublicLeadForm
from .services import lead_form as lf


def public_lead_form(request):
    """
    GET:  render the standalone HTML form.
    POST: validate + create lead, return thank-you page or JSON.
    CSRF-exempt: this endpoint is meant to be embedded cross-origin.
    """
    utm = lf.extract_utm(request)
    want_json = (
        request.method == 'POST'
        and ('application/json' in request.META.get('HTTP_ACCEPT', '')
             or request.POST.get('format') == 'json')
    )

    if request.method == 'GET':
        form = PublicLeadForm()
        return render(request, 'marketing/public_lead_form.html', {
            'form': form, 'utm': utm, 'success': False,
        })

    # --- POST ---
    # 1) Honeypot check (silent discard)
    if lf.is_honeypot_filled(request.POST):
        if want_json:
            return JsonResponse({'ok': True, 'message': 'Thank you.'})
        return render(request, 'marketing/public_lead_form.html', {
            'form': PublicLeadForm(), 'utm': utm, 'success': True,
        })

    # 2) Rate limit
    ip = lf._client_ip(request)
    if lf.rate_limited(ip):
        msg = 'Too many submissions from this network. Please try again later.'
        if want_json:
            return JsonResponse({'ok': False, 'error': msg}, status=429)
        return render(request, 'marketing/public_lead_form.html', {
            'form': PublicLeadForm(), 'utm': utm, 'error': msg,
        }, status=429)

    # 3) Validate
    form = PublicLeadForm(request.POST)
    if not form.is_valid():
        if want_json:
            return JsonResponse({'ok': False, 'errors': form.errors}, status=400)
        return render(request, 'marketing/public_lead_form.html', {
            'form': form, 'utm': utm, 'success': False,
        }, status=400)

    # 4) Persist
    try:
        result = lf.create_lead_from_form(
            request=request,
            cleaned=form.cleaned_data,
            utm=utm,
        )
    except Exception as exc:
        logger.exception('Public lead form save failed')
        msg = 'Something went wrong on our side. Please try again.'
        if want_json:
            return JsonResponse({'ok': False, 'error': msg}, status=500)
        return render(request, 'marketing/public_lead_form.html', {
            'form': form, 'utm': utm, 'error': msg,
        }, status=500)

    if want_json:
        return JsonResponse({
            'ok': True,
            'lead_id': result['lead'].pk,
            'created': result['created'],
            'matched_existing': result['matched_existing'],
            'message': 'Thank you — we will get back to you shortly.',
        }, status=201 if result['created'] else 200)

    return render(request, 'marketing/public_lead_form.html', {
        'form': PublicLeadForm(),
        'utm': utm,
        'success': True,
    })


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def public_lead_form_api(request):
    """JSON-only variant for SPAs / JS widgets. Same logic, always JSON."""
    return public_lead_form(request)




# =========================================================================
# Weekly report — web preview + send-now
# =========================================================================
from django.core.management import call_command
from marketing.services import weekly_report as wr


@marketing_access
def weekly_report_preview(request):
    """Preview the report in the browser + send-now button."""
    days = int(request.GET.get('days', 7))
    if days not in (7, 14, 30):
        days = 7

    if request.method == 'POST':
        try:
            sent, error = wr.send(days=days)
            if error:
                messages.error(request, f'Send failed: {error}')
            else:
                messages.success(request, f'Weekly report sent to {sent} recipient(s).')
        except Exception as exc:
            messages.error(request, f'Unexpected error: {exc}')
        return redirect(f'{request.path}?days={days}')

    data = wr.collect(days=days)
    recipients = wr.default_recipients()
    return render(request, 'marketing/weekly_report_preview.html', {
        'active': 'marketing',
        'data': data,
        'days': days,
        'recipients': recipients,
    })