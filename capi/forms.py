from django import forms

from . import constants as C
from .models import ConversionEventConfig, LeadTracking, PlatformConnection


class ConnectionForm(forms.ModelForm):
    access_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False,
                                    attrs={'autocomplete': 'new-password'}),
        help_text='Stored encrypted. Leave blank to keep the current token.')
    clear_token = forms.BooleanField(required=False, label='Remove stored token')

    page_access_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False,
                                    attrs={'autocomplete': 'new-password'}),
        help_text='Page Access Token from Meta App. Stored encrypted. Leave blank to keep current.')

    class Meta:
        model = PlatformConnection
        fields = [
            'is_active', 'name', 'api_version',
            'dataset_id', 'test_event_code', 'lead_event_source',
            'page_id', 'webhook_verify_token', 'webhook_subscribed_at',
            'ad_account_id',
            'linkedin_organization_urn', 'linkedin_webhook_verify_token',
            'token_expires_at',
        ]
        widgets = {
            'token_expires_at': forms.DateTimeInput(
                attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'webhook_subscribed_at': forms.DateTimeInput(
                attrs={'type': 'datetime-local', 'readonly': 'readonly'},
                format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, platform=None, **kwargs):
        super().__init__(*args, **kwargs)
        platform = platform or self.instance.platform

        if 'token_expires_at' in self.fields:
            self.fields['token_expires_at'].input_formats = ['%Y-%m-%dT%H:%M']
        if 'webhook_subscribed_at' in self.fields:
            self.fields['webhook_subscribed_at'].required = False
            self.fields['webhook_subscribed_at'].disabled = True

        if platform == C.PLATFORM_META:
            drop = ['ad_account_id',
                    'linkedin_organization_urn', 'linkedin_webhook_verify_token']
        else:
            drop = ['dataset_id', 'test_event_code', 'lead_event_source',
                    'page_id', 'webhook_verify_token', 'webhook_subscribed_at',
                    'page_access_token']
        for f in drop:
            self.fields.pop(f, None)

        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault('class', 'form-control')

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get('clear_token'):
            obj.set_token('')
        elif self.cleaned_data.get('access_token'):
            obj.set_token(self.cleaned_data['access_token'])
        page_token = self.cleaned_data.get('page_access_token')
        if page_token:
            obj.set_page_token(page_token)
        if commit:
            obj.save()
        return obj


class ConfigForm(forms.ModelForm):
    class Meta:
        model = ConversionEventConfig
        fields = ['name', 'connection', 'is_enabled', 'trigger_key', 'from_value', 'to_value',
                  'min_score', 'platform_event_name', 'linkedin_conversion_urn',
                  'linkedin_campaign_urns', 'send_value', 'fixed_value', 'currency',
                  'require_consent', 'require_platform_match']
        widgets = {'linkedin_campaign_urns': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault('class', 'form-control')


class TrackingForm(forms.ModelForm):
    class Meta:
        model = LeadTracking
        exclude = ['lead', 'consent_updated_at', 'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs.setdefault('class', 'form-control')