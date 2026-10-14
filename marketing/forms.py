"""Marketing Center forms — used by the Marketing Center UI directly.
Django Admin is not required for these workflows."""
from django import forms

from .models import (ProspectSource, Campaign, MarketingContent,
                     MarketingConsent, SuppressionEntry, Referral,
                     AutomationRun, AutomationStep)
from . import constants as C


class BootstrapMixin:
    """Adds Bootstrap-style classes to every widget."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, (forms.CheckboxInput, forms.RadioSelect)):
                continue
            if isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs.setdefault('class', 'form-control')
            elif isinstance(w, forms.Textarea):
                w.attrs.setdefault('class', 'form-control')
                w.attrs.setdefault('rows', 4)
            else:
                w.attrs.setdefault('class', 'form-control')


class ProspectSourceForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ProspectSource
        fields = ['name', 'kind', 'description', 'is_active',
                  'cost_per_lead', 'utm_source', 'utm_medium', 'utm_campaign']


class CampaignForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Campaign
        fields = ['name', 'slug', 'description', 'source', 'channel', 'status',
                  'owner', 'budget_amount', 'budget_currency',
                  'start_date', 'end_date', 'target_leads', 'target_revenue', 'notes']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date':   forms.DateInput(attrs={'type': 'date'}),
        }

    def clean(self):
        cleaned = super().clean()
        s, e = cleaned.get('start_date'), cleaned.get('end_date')
        if s and e and e < s:
            self.add_error('end_date', 'End date cannot be before start date.')
        return cleaned


class MarketingContentForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = MarketingContent
        fields = ['campaign', 'kind', 'slug', 'title', 'subject',
                  'body', 'body_html', 'media_url', 'language',
                  'tags', 'is_active', 'version', 'parent',
                  'planned_date', 'published_url', 'published_at',]


class MarketingConsentForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = MarketingConsent
        fields = ['lead', 'channel', 'purpose', 'status', 'source',
                  'source_ref', 'evidence', 'captured_ip', 'captured_user_agent']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Limit lead dropdown to a manageable size
        from leads.models import Lead
        self.fields['lead'].queryset = Lead.objects.order_by('-created_at')[:500]


class SuppressionEntryForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = SuppressionEntry
        fields = ['channel', 'value', 'reason', 'notes', 'expires_at', 'lead']
        widgets = {
            'expires_at': forms.DateTimeInput(
                attrs={'type': 'datetime-local'}
            ),
            'planned_date': forms.DateInput(
                attrs={'type': 'date'}
            ),
            'published_at': forms.DateTimeInput(
                attrs={'type': 'datetime-local'}
            ),
        }

    def clean(self):
        cleaned = super().clean()
        ch, val = cleaned.get('channel'), cleaned.get('value')

        if ch and val:
            from .services.consent import normalize_value
            cleaned['value'] = normalize_value(ch, val)

        return cleaned


class ReferralForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Referral
        fields = ['referrer_lead', 'referred_lead', 'referrer_kind', 'referrer_label',
                  'campaign', 'status', 'reward_amount', 'reward_currency',
                  'reward_paid_at', 'notes']
        widgets = {'reward_paid_at': forms.DateTimeInput(attrs={'type': 'datetime-local'})}

    def clean(self):
        cleaned = super().clean()
        ref = cleaned.get('referrer_lead')
        rec = cleaned.get('referred_lead')
        if ref and rec and ref.pk == rec.pk:
            self.add_error('referred_lead', 'A lead cannot refer itself.')
        return cleaned


class AutomationRunForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = AutomationRun
        fields = ['name', 'campaign', 'lead', 'content', 'status',
                  'current_step', 'next_run_at', 'payload']
        widgets = {'next_run_at': forms.DateTimeInput(attrs={'type': 'datetime-local'})}


class AutomationStepForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = AutomationStep
        fields = ['order', 'kind', 'delay_seconds', 'content', 'params']
            
# =========================================================================
# Prospect management
# =========================================================================

class ProspectForm(BootstrapMixin, forms.ModelForm):
    """Create/edit a prospect — same Lead model, marketing-focused fields."""
    class Meta:
        from leads.models import Lead
        model = Lead
        fields = [
            'name', 'contact_number', 'email', 'country', 'city', 'address',
            'lead_source', 'marketing_source', 'status', 'spo',
            'source_url', 'research_date', 'business_requirement', 'interested_service',
            'detail',
        ]
        widgets = {
            'research_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # show newest sources first
        self.fields['marketing_source'].queryset = (
            ProspectSource.objects.filter(is_active=True).order_by('kind', 'name')
        )
        self.fields['marketing_source'].required = False
        self.fields['interested_service'].required = False
        self.fields['research_date'].required = False
        self.fields['country'].required = False
        self.fields['city'].required = False
        self.fields['address'].required = False
        self.fields['detail'].required = False


class ProspectImportForm(BootstrapMixin, forms.Form):
    """First step — upload a CSV/XLSX file."""
    file = forms.FileField(
        label='CSV or Excel file',
        help_text='Max 5 MB. First row must contain column headers.',
        widget=forms.ClearableFileInput(attrs={'accept': '.csv,.xlsx,.xls'}),
    )
    source = forms.ModelChoiceField(
        queryset=ProspectSource.objects.filter(is_active=True).order_by('kind', 'name'),
        label='Assign all imported prospects to this source',
        required=False,
    )
    default_status = forms.ChoiceField(
        choices=[('new', 'New'), ('positive', 'Positive')],
        initial='new',
        label='Default status',
    )

    def clean_file(self):
        f = self.cleaned_data['file']
        if f.size > 5 * 1024 * 1024:
            raise forms.ValidationError('File too large — 5 MB max.')
        name = f.name.lower()
        if not (name.endswith('.csv') or name.endswith('.xlsx') or name.endswith('.xls')):
            raise forms.ValidationError('Only .csv, .xlsx or .xls files are allowed.')
        return f     
    
    
    
    
# =========================================================================
# Public website lead form (no auth)
# =========================================================================
class PublicLeadForm(BootstrapMixin, forms.Form):
    """Public-facing lead form. Rendered on the website, not in the CRM UI."""
    name = forms.CharField(max_length=150, label='Your name')
    email = forms.EmailField(required=False, label='Email')
    phone = forms.CharField(max_length=30, required=False, label='Phone')
    company = forms.CharField(max_length=200, required=False, label='Company')
    industry = forms.CharField(max_length=100, required=False, label='Industry')
    budget = forms.CharField(max_length=50, required=False, label='Budget range')
    message = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4}),
        required=False, label='What do you need help with?',
    )

    # Honeypot — hidden in template, filled only by bots
    website_url = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get('name') or '').strip()
        email = (cleaned.get('email') or '').strip().lower()
        phone = (cleaned.get('phone') or '').strip()

        if not name:
            self.add_error('name', 'Please enter your name.')
        if not email and not phone:
            self.add_error('email', 'Please provide either an email or a phone number.')

        if email:
            import re
            if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
                self.add_error('email', 'Please enter a valid email address.')

        return cleaned# =========================================================================
# Public website lead form (no auth)
# =========================================================================
class PublicLeadForm(BootstrapMixin, forms.Form):
    """Public-facing lead form. Rendered on the website, not in the CRM UI."""
    name = forms.CharField(max_length=150, label='Your name')
    email = forms.EmailField(required=False, label='Email')
    phone = forms.CharField(max_length=30, required=False, label='Phone')
    company = forms.CharField(max_length=200, required=False, label='Company')
    industry = forms.CharField(max_length=100, required=False, label='Industry')
    budget = forms.CharField(max_length=50, required=False, label='Budget range')
    message = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4}),
        required=False, label='What do you need help with?',
    )

    # Honeypot — hidden in template, filled only by bots
    website_url = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get('name') or '').strip()
        email = (cleaned.get('email') or '').strip().lower()
        phone = (cleaned.get('phone') or '').strip()

        if not name:
            self.add_error('name', 'Please enter your name.')
        if not email and not phone:
            self.add_error('email', 'Please provide either an email or a phone number.')

        if email:
            import re
            if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
                self.add_error('email', 'Please enter a valid email address.')

        return cleaned   
    
    
    
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