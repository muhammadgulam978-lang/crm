from django import forms

from . import constants as C
from .models import ConversionEventConfig, LeadTracking, PlatformConnection


class ConnectionForm(forms.ModelForm):
    access_token = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
        help_text='Stored encrypted. Leave blank to keep the current token.')
    clear_token = forms.BooleanField(required=False, label='Remove stored token')

    class Meta:
        model = PlatformConnection
        fields = ['is_active', 'name', 'api_version', 'dataset_id', 'test_event_code',
                  'lead_event_source', 'ad_account_id', 'token_expires_at']
        widgets = {'token_expires_at': forms.DateTimeInput(attrs={'type': 'datetime-local'},
                                                           format='%Y-%m-%dT%H:%M')}

    def __init__(self, *args, platform=None, **kwargs):
        super().__init__(*args, **kwargs)
        platform = platform or self.instance.platform
        drop = (['ad_account_id'] if platform == C.PLATFORM_META
                else ['dataset_id', 'test_event_code', 'lead_event_source'])
        for f in drop:
            self.fields.pop(f)
        self.fields['token_expires_at'].input_formats = ['%Y-%m-%dT%H:%M']
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault('class', 'form-control')

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get('clear_token'):
            obj.set_token('')
        elif self.cleaned_data.get('access_token'):
            obj.set_token(self.cleaned_data['access_token'])
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
