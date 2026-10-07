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
                  'tags', 'is_active', 'version', 'parent'
                  'status', 'planned_date', 'published_url', 'published_at',]


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
        widgets = {'expires_at': forms.DateTimeInput(attrs={'type': 'datetime-local'})
                   'planned_date': forms.DateInput(attrs={'type': 'date'}),
                   'published_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
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