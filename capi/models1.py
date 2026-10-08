from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from datetime import timedelta

from . import constants as C
from .crypto import decrypt, encrypt


class PlatformConnection(models.Model):
    """One server-side connection per ad platform (Meta / LinkedIn)."""
    platform = models.CharField(max_length=20, choices=C.PLATFORM_CHOICES, unique=True)
    name = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=False)
    api_version = models.CharField(
        max_length=20, blank=True,
        help_text='Leave blank for the default (Meta e.g. v25.0, LinkedIn e.g. 202606).')
    
        # ---- Meta Lead Ads (webhook receiver) ----
    page_id = models.CharField(
        'Facebook Page ID', max_length=64, blank=True,
        help_text='The Facebook Page whose Lead Ads we receive.'
    )
    page_access_token_enc = models.TextField(
        blank=True,
        help_text='Page Access Token (encrypted). Required to fetch lead details.'
    )
    page_token_hint = models.CharField(max_length=8, blank=True)
    webhook_verify_token = models.CharField(
        max_length=64, blank=True,
        help_text='Random string you set in Meta App > Webhooks. Used for the initial GET handshake.'
    )
    webhook_subscribed_at = models.DateTimeField(null=True, blank=True)
    
    def set_page_token(self, raw):
        raw = (raw or '').strip()
        self.page_access_token_enc = encrypt(raw)
        self.page_token_hint = raw[-4:] if raw else ''

    def get_page_token(self):
        return decrypt(self.page_access_token_enc)

    # Meta
    dataset_id = models.CharField('Dataset / Pixel ID', max_length=64, blank=True)
    test_event_code = models.CharField(max_length=50, blank=True,
                                       help_text='Meta Test Events code. Remove for live traffic.')
    lead_event_source = models.CharField(max_length=100, default='SIS AI CRM')

    # LinkedIn
    ad_account_id = models.CharField('Ad account ID', max_length=64, blank=True)

    # Credentials (encrypted at rest; never rendered back to the UI)
    access_token_enc = models.TextField(blank=True)
    token_hint = models.CharField(max_length=8, blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)

    # API health
    health_status = models.CharField(max_length=10, default='unknown')  # unknown / ok / error
    health_message = models.TextField(blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    token_invalid = models.BooleanField(default=False)  # set when the API rejects the token

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.get_platform_display()

    # ---- credentials ----
    def set_token(self, raw):
        raw = (raw or '').strip()
        self.access_token_enc = encrypt(raw)
        self.token_hint = raw[-4:] if raw else ''
        self.token_invalid = False

    def get_token(self):
        token = decrypt(self.access_token_enc)
        if token:
            return token
        env_name = ('CAPI_META_ACCESS_TOKEN' if self.platform == C.PLATFORM_META
                    else 'CAPI_LINKEDIN_ACCESS_TOKEN')
        return getattr(settings, env_name, '') or ''

    @property
    def effective_api_version(self):
        if self.api_version:
            return self.api_version
        if self.platform == C.PLATFORM_META:
            return getattr(settings, 'CAPI_META_API_VERSION', 'v25.0')
        return getattr(settings, 'CAPI_LINKEDIN_API_VERSION', '202606')

    @property
    def token_status(self):
        if not self.get_token():
            return 'missing'
        if self.token_invalid:
            return 'rejected'
        if self.token_expires_at:
            now = timezone.now()
            if self.token_expires_at <= now:
                return 'expired'
            if self.token_expires_at <= now + timedelta(days=7):
                return 'expiring'
        return 'ok'

    @property
    def is_configured(self):
        if self.platform == C.PLATFORM_META:
            return bool(self.dataset_id and self.get_token())
        return bool(self.get_token())

    @property
    def is_ready(self):
        return self.is_active and self.is_configured and self.token_status in ('ok', 'expiring')


class LeadTracking(models.Model):
    """Attribution + consent for a CRM lead. Latest touch is kept here;
    every touch is also recorded in LeadTouchpoint."""
    lead = models.OneToOneField('leads.Lead', on_delete=models.CASCADE,
                                related_name='capi_tracking')
    platform = models.CharField(max_length=20, choices=C.TRACKING_PLATFORM_CHOICES, blank=True)
    campaign_id = models.CharField(max_length=100, blank=True)
    campaign_name = models.CharField(max_length=255, blank=True)
    adset_id = models.CharField(max_length=100, blank=True)
    ad_id = models.CharField(max_length=100, blank=True)
    form_id = models.CharField(max_length=100, blank=True)
    external_lead_id = models.CharField(
        max_length=100, blank=True,
        help_text='Meta Lead Ads lead ID / LinkedIn lead ID from Lead Sync.')

    consent_status = models.CharField(max_length=10, choices=C.CONSENT_CHOICES,
                                      default=C.CONSENT_UNKNOWN)
    consent_updated_at = models.DateTimeField(null=True, blank=True)
    

    # Client identifiers - only fill when legitimately captured with consent
    fbc = models.CharField('Meta click ID (fbc)', max_length=255, blank=True)
    fbp = models.CharField('Meta browser ID (fbp)', max_length=255, blank=True)
    li_fat_id = models.CharField('LinkedIn click ID (li_fat_id)', max_length=255, blank=True)
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Tracking for lead #{self.lead_id}'

    def save(self, *args, **kwargs):
        if self.pk:
            old = LeadTracking.objects.filter(pk=self.pk).values_list('consent_status', flat=True).first()
            if old != self.consent_status:
                self.consent_updated_at = timezone.now()
        elif self.consent_status != C.CONSENT_UNKNOWN:
            self.consent_updated_at = timezone.now()
        super().save(*args, **kwargs)


class LeadTouchpoint(models.Model):
    """Every ad interaction of a person - so repeat interest never creates duplicate leads."""
    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='capi_touchpoints')
    platform = models.CharField(max_length=20, blank=True)
    campaign_id = models.CharField(max_length=100, blank=True)
    campaign_name = models.CharField(max_length=255, blank=True)
    adset_id = models.CharField(max_length=100, blank=True)
    ad_id = models.CharField(max_length=100, blank=True)
    form_id = models.CharField(max_length=100, blank=True)
    external_lead_id = models.CharField(max_length=100, blank=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']


class ConversionEventConfig(models.Model):
    """Admin-approved mapping: CRM trigger -> platform conversion event.
    Nothing is sent unless a config exists AND is enabled."""
    name = models.CharField(max_length=150)
    connection = models.ForeignKey(PlatformConnection, on_delete=models.CASCADE,
                                   related_name='event_configs')
    is_enabled = models.BooleanField(default=False)

    trigger_key = models.CharField(max_length=40, choices=C.TRIGGER_CHOICES)
    from_value = models.CharField(max_length=50, blank=True,
                                  help_text='Only fire when the previous value equals this (blank = any).')
    to_value = models.CharField(max_length=50, blank=True,
                                help_text='Only fire when the new value equals this (blank = any).')
    min_score = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Lead score gate (0-100). For "score reached" it is the threshold; '
                  'for other triggers the lead must already have at least this score.')

    platform_event_name = models.CharField(
        max_length=100, blank=True,
        help_text='Meta event_name, e.g. Lead, QualifiedLead, Purchase. Not used by LinkedIn.')
    linkedin_conversion_urn = models.CharField(
        max_length=100, blank=True, help_text='urn:lla:llaPartnerConversion:<rule id>')
    linkedin_campaign_urns = models.TextField(
        blank=True, help_text='urn:li:sponsoredCampaign:<id> - one per line. '
                              'The conversion rule can only attribute to associated campaigns.')

    send_value = models.BooleanField(default=False)
    fixed_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                      help_text='Overrides the CRM value when set.')
    currency = models.CharField(max_length=3, blank=True, help_text='ISO code, e.g. USD, PKR, AED.')

    require_consent = models.BooleanField(default=True)
    require_platform_match = models.BooleanField(
        default=True,
        help_text='Only send for leads whose attribution platform matches this connection.')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['connection__platform', 'name']

    def __str__(self):
        return self.name

    @property
    def platform(self):
        return self.connection.platform

    @property
    def effective_currency(self):
        return (self.currency or getattr(settings, 'CAPI_DEFAULT_CURRENCY', 'USD')).upper()

    def clean(self):
        if self.trigger_key == C.TRIGGER_LEAD_SCORE and not self.min_score:
            raise ValidationError({'min_score': 'A score threshold is required for this trigger.'})
        if self.connection_id:
            if self.connection.platform == C.PLATFORM_META and not self.platform_event_name:
                raise ValidationError({'platform_event_name': 'Meta needs an event name.'})
            if self.connection.platform == C.PLATFORM_LINKEDIN:
                urn = self.linkedin_conversion_urn
                if not urn.startswith('urn:lla:llaPartnerConversion:'):
                    raise ValidationError({
                        'linkedin_conversion_urn': 'Must look like urn:lla:llaPartnerConversion:123'})
        if self.send_value and self.trigger_key not in C.VALUE_TRIGGERS and self.fixed_value is None:
            raise ValidationError({'fixed_value': 'This trigger has no CRM value; set a fixed value '
                                                  'or untick "Send value".'})


class ConversionEvent(models.Model):
    """A queued/sent conversion. Created ONLY for eligible events."""
    config = models.ForeignKey(ConversionEventConfig, on_delete=models.PROTECT, related_name='events')
    platform = models.CharField(max_length=20, choices=C.PLATFORM_CHOICES)
    lead = models.ForeignKey('leads.Lead', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='conversion_events')
    lead_ref = models.PositiveIntegerField(null=True, blank=True)
    lead_label = models.CharField(max_length=150, blank=True)

    trigger_key = models.CharField(max_length=40)
    source_model = models.CharField(max_length=40, blank=True)
    source_id = models.CharField(max_length=40, blank=True)
    dedup_key = models.CharField(max_length=80)
    event_id = models.CharField(max_length=64, unique=True)  # CRM event ID sent to the platform
    event_name = models.CharField(max_length=100)
    event_time = models.DateTimeField()
    value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)

    # attribution snapshot (queryable)
    campaign_id = models.CharField(max_length=100, blank=True)
    campaign_name = models.CharField(max_length=255, blank=True)
    adset_id = models.CharField(max_length=100, blank=True)
    ad_id = models.CharField(max_length=100, blank=True)

    status = models.CharField(max_length=12, choices=C.STATUS_CHOICES, default=C.ST_PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    identifiers_used = models.JSONField(default=list, blank=True)   # e.g. ["em","ph","lead_id"]
    request_payload = models.JSONField(null=True, blank=True)      # hashed identifiers only, no token
    response_status = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    platform_trace_id = models.CharField(max_length=100, blank=True)
    events_received = models.IntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=50, blank=True)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['config', 'dedup_key'], name='capi_unique_event_per_config'),
        ]
        indexes = [models.Index(fields=['status', 'next_attempt_at'])]

    def __str__(self):
        return f'{self.event_name} -> {self.get_platform_display()} ({self.status})'


class ConversionEventLog(models.Model):
    """One row per delivery attempt (append-only)."""
    event = models.ForeignKey(ConversionEvent, on_delete=models.CASCADE, related_name='logs')
    attempt = models.PositiveSmallIntegerField()
    success = models.BooleanField(default=False)
    status_code = models.IntegerField(null=True, blank=True)
    message = models.TextField(blank=True)
    response_excerpt = models.TextField(blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']



class MetaWebhookEvent(models.Model):
    """
    One row per POST we receive from Meta. This is the idempotency anchor:
    if a POST with the same leadgen_id arrives twice, we skip it here.
    """
    leadgen_id = models.CharField(max_length=32, unique=True, db_index=True)
    page_id = models.CharField(max_length=64, blank=True)
    form_id = models.CharField(max_length=64, blank=True)
    ad_id = models.CharField(max_length=64, blank=True)
    adset_id = models.CharField(max_length=64, blank=True)
    campaign_id = models.CharField(max_length=64, blank=True)

    raw_payload = models.JSONField(default=dict, blank=True)
    processed = models.BooleanField(default=False, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    lead = models.ForeignKey(
        'leads.Lead', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='meta_webhook_events',
    )
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Meta Lead Ads event'

    def __str__(self):
        return f'leadgen {self.leadgen_id}'