"""
Marketing Center — additive foundation.

Design rules:
  * Reuse leads.Lead / Contact / Company / Opportunity / GeneralTask / FollowUp / EmailLog
    and capi.LeadTracking. Do not duplicate them.
  * Every FK to an existing table is nullable + SET_NULL so nothing is ever cascade-deleted
    by a marketing operation.
  * All models are new; no existing table is altered except Lead.marketing_source (nullable FK).
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from . import constants as C

User = settings.AUTH_USER_MODEL


# --------------------------------------------------------------------------- #
# 1. Prospect source dimension
# --------------------------------------------------------------------------- #
class ProspectSource(models.Model):
    """
    A marketing-source dimension (referral programme, webinar, partner, cold list, ...).
    Distinct from Lead.lead_source which is a fixed 11-value CRM enum.
    """
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=20, choices=C.SOURCE_KIND_CHOICES, default=C.SOURCE_KIND_OTHER)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    cost_per_lead = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    utm_source = models.CharField(max_length=100, blank=True)
    utm_medium = models.CharField(max_length=100, blank=True)
    utm_campaign = models.CharField(max_length=150, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['kind', 'name']
        constraints = [
            models.UniqueConstraint(fields=['name', 'kind'], name='mkt_unique_source_name_kind'),
        ]
        indexes = [models.Index(fields=['kind', 'is_active'])]

    def __str__(self):
        return f'{self.name} ({self.get_kind_display()})'


# --------------------------------------------------------------------------- #
# 2. Marketing campaign
# --------------------------------------------------------------------------- #
class Campaign(models.Model):
    """
    A marketing campaign. Distinct from capi ad-attribution strings
    (ConversionEvent.campaign_id / LeadTracking.campaign_name).
    """
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    source = models.ForeignKey(ProspectSource, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='campaigns')
    channel = models.CharField(max_length=20, choices=C.CAMPAIGN_CHANNEL_CHOICES,
                               default=C.CAMPAIGN_CHANNEL_MIXED)
    status = models.CharField(max_length=15, choices=C.CAMPAIGN_STATUS_CHOICES,
                              default=C.CAMPAIGN_STATUS_DRAFT, db_index=True)

    owner = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name='owned_campaigns')
    team = models.ManyToManyField(User, blank=True, related_name='marketing_campaigns')

    budget_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    budget_currency = models.CharField(max_length=3, default='PKR')
    spend_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    target_leads = models.PositiveIntegerField(default=0)
    target_revenue = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'start_date']),
            models.Index(fields=['channel', 'status']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True) | models.Q(start_date__isnull=True)
                      | models.Q(end_date__gte=models.F('start_date')),
                name='mkt_campaign_end_after_start',
            ),
            models.CheckConstraint(
                condition=models.Q(budget_amount__isnull=True) | models.Q(budget_amount__gte=0),
                name='mkt_campaign_budget_nonneg',
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})


# --------------------------------------------------------------------------- #
# 3. Marketing content (templates, scripts, posts)
# --------------------------------------------------------------------------- #
class MarketingContent(models.Model):
    """
    Reusable content variant tied to a campaign.
    Actual send records reuse leads.EmailLog; the WhatsApp/Call logs reuse
    leads.CommunicationLog / chatbot.ChatMessage.
    """
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='contents')
    kind = models.CharField(max_length=20, choices=C.CONTENT_KIND_CHOICES)
    slug = models.SlugField(max_length=120)
    title = models.CharField(max_length=200)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    media_url = models.URLField(blank=True)
    language = models.CharField(max_length=10, default='en')
    tags = models.JSONField(default=list, blank=True)

    is_active = models.BooleanField(default=True, db_index=True)
    version = models.PositiveIntegerField(default=1)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='variants')

    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
        # Workflow
    STATUS_DRAFT = 'draft'
    STATUS_IN_REVIEW = 'in_review'
    STATUS_APPROVED = 'approved'
    STATUS_PUBLISHED = 'published'
    STATUS_ARCHIVED = 'archived'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_IN_REVIEW, 'In Review'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_PUBLISHED, 'Published'),
        (STATUS_ARCHIVED, 'Archived'),
    ]
    status = models.CharField(
        max_length=15, choices=STATUS_CHOICES,
        default=STATUS_DRAFT, db_index=True,
    )
    planned_date = models.DateField(null=True, blank=True, db_index=True)
    published_url = models.URLField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    ai_generated = models.BooleanField(default=False)
    ai_model = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ['campaign', 'kind', 'slug']
        constraints = [
            models.UniqueConstraint(fields=['campaign', 'slug'], name='mkt_unique_content_per_campaign'),
        ]
        indexes = [models.Index(fields=['campaign', 'kind', 'is_active'])]
        
        indexes = [models.Index(fields=['campaign', 'kind', 'is_active']),
                   models.Index(fields=['status', 'planned_date']),]

    def __str__(self):
        return f'{self.campaign.name} / {self.title}'


# --------------------------------------------------------------------------- #
# 4. Consent ledger (channel × purpose) — wider scope than capi.LeadTracking
# --------------------------------------------------------------------------- #
class MarketingConsent(models.Model):
    """
    Channel-and-purpose consent per lead. Complements capi.LeadTracking.consent_status
    (which is per-ad-platform). This one is the source of truth for outreach gating.
    """
    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='marketing_consents')
    channel = models.CharField(max_length=20, choices=C.CHANNEL_CHOICES, db_index=True)
    purpose = models.CharField(max_length=20, choices=C.PURPOSE_CHOICES,
                               default=C.PURPOSE_MARKETING, db_index=True)
    status = models.CharField(max_length=12, choices=C.CONSENT_STATUS_CHOICES,
                              default=C.CONSENT_UNKNOWN, db_index=True)
    source = models.CharField(max_length=30, choices=C.CONSENT_SOURCE_CHOICES, blank=True)
    source_ref = models.CharField(max_length=120, blank=True,
                                  help_text='Form URL, import batch id, ticket id, etc.')
    evidence = models.TextField(blank=True)
    captured_ip = models.GenericIPAddressField(null=True, blank=True)
    captured_user_agent = models.CharField(max_length=500, blank=True)

    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['lead', 'channel', 'purpose'],
                                    name='mkt_unique_consent_per_lead_channel_purpose'),
        ]
        indexes = [
            models.Index(fields=['lead', 'status']),
            models.Index(fields=['channel', 'purpose', 'status']),
        ]

    def __str__(self):
        return f'Lead#{self.lead_id} {self.channel}/{self.purpose} = {self.status}'


# --------------------------------------------------------------------------- #
# 5. Suppression list
# --------------------------------------------------------------------------- #
class SuppressionEntry(models.Model):
    """
    Do-not-contact registry, independent of Lead rows so it survives lead deletion.
    `value` is always normalised (lowercased email, digits-only phone).
    """
    channel = models.CharField(max_length=20, choices=C.CHANNEL_CHOICES, db_index=True)
    value = models.CharField(max_length=255, db_index=True,
                             help_text='Normalised email or phone digits.')
    reason = models.CharField(max_length=20, choices=C.SUPPRESSION_REASON_CHOICES)
    notes = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    lead = models.ForeignKey('leads.Lead', null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='marketing_suppressions')
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['channel', 'value'],
                                    name='mkt_unique_suppression_channel_value'),
        ]
        indexes = [models.Index(fields=['channel', 'value', 'expires_at'])]

    def __str__(self):
        return f'{self.channel}:{self.value} ({self.reason})'

    @property
    def is_active(self):
        return self.expires_at is None or self.expires_at > timezone.now()


# --------------------------------------------------------------------------- #
# 6. Automation — DB-driven follow-up state (no Celery dependency)
# --------------------------------------------------------------------------- #
class AutomationRun(models.Model):
    """
    One automation instance attached to a lead (optionally under a campaign).
    A management command polls this table and advances steps — same pattern as
    capi's process_conversion_queue.
    """
    name = models.CharField(max_length=200, blank=True)
    campaign = models.ForeignKey(Campaign, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='automation_runs')
    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='marketing_automations')
    content = models.ForeignKey(MarketingContent, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='runs')

    status = models.CharField(max_length=12, choices=C.AUTOMATION_STATUS_CHOICES,
                              default=C.AUTOMATION_STATUS_PENDING, db_index=True)
    current_step = models.PositiveIntegerField(default=0)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)

    next_run_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    payload = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['next_run_at']
        indexes = [
            models.Index(fields=['status', 'next_run_at']),
            models.Index(fields=['lead', 'status']),
        ]

    def __str__(self):
        return f'Automation#{self.pk} for lead {self.lead_id} ({self.status})'


class AutomationStep(models.Model):
    """Ordered definition of what an automation does."""
    run = models.ForeignKey(AutomationRun, on_delete=models.CASCADE, related_name='steps')
    order = models.PositiveIntegerField()
    kind = models.CharField(max_length=20, choices=C.STEP_KIND_CHOICES)
    delay_seconds = models.PositiveIntegerField(default=0)
    content = models.ForeignKey(MarketingContent, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='steps')
    params = models.JSONField(default=dict, blank=True)

    executed_at = models.DateTimeField(null=True, blank=True)
    success = models.BooleanField(default=False)
    result = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['run', 'order']
        constraints = [
            models.UniqueConstraint(fields=['run', 'order'], name='mkt_unique_step_order_per_run'),
        ]

    def __str__(self):
        return f'Run#{self.run_id} step {self.order} ({self.kind})'


# --------------------------------------------------------------------------- #
# 7. Referrals
# --------------------------------------------------------------------------- #
class Referral(models.Model):
    """
    A referral record: who referred whom, and the reward state.
    `referrer_lead` may be null when the referrer is a partner / staff, in which
    case `referrer_kind` + `referrer_label` carry the identity.
    """
    referrer_lead = models.ForeignKey('leads.Lead', null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='referrals_made')
    referred_lead = models.ForeignKey('leads.Lead', null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='referrals_received')
    referrer_kind = models.CharField(max_length=10, choices=C.REFERRER_KIND_CHOICES,
                                     default=C.REFERRER_KIND_LEAD)
    referrer_label = models.CharField(max_length=200, blank=True,
                                      help_text='Name / email / org when the referrer is not a Lead.')

    campaign = models.ForeignKey(Campaign, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='referrals')
    status = models.CharField(max_length=12, choices=C.REFERRAL_STATUS_CHOICES,
                              default=C.REFERRAL_STATUS_PENDING, db_index=True)
    reward_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reward_currency = models.CharField(max_length=3, default='PKR')
    reward_paid_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['referrer_lead', 'referred_lead'],
                condition=models.Q(referrer_lead__isnull=False, referred_lead__isnull=False),
                name='mkt_unique_referral_pair',
            ),
            models.CheckConstraint(
                condition=~models.Q(referrer_lead=models.F('referred_lead')) | models.Q(referrer_lead__isnull=True),
                name='mkt_referral_no_self_referral',
            ),
        ]
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['referrer_lead', 'status']),
        ]

    def __str__(self):
        return f'Referral {self.referrer_lead_id or self.referrer_label} -> {self.referred_lead_id}'
    
    
    
class LinkedInCredential(models.Model):
    """Stores OAuth tokens for LinkedIn organic posting per CRM user."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='linkedin_credential')
    access_token = models.TextField()
    refresh_token = models.TextField(blank=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    linkedin_user_id = models.CharField(max_length=64, blank=True)
    linkedin_name = models.CharField(max_length=200, blank=True)
    linkedin_email = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'LinkedIn credentials for {self.user}'
    
    
