"""
AI Sales Assistant - data model.

Nothing here replaces an existing CRM table. Leads, Contacts, Opportunities, Meetings,
Follow-ups, Tasks and Communication logs stay the source of truth; these tables only add:
  * CustomerFact      - facts about the customer, each marked verified / assumed
  * LeadInsight       - latest qualification, recommendation and priority for a lead
  * NextAction        - Current stage / Next action / Owner / Due date (+ AI override tracking)
  * OverrideLog       - every time a salesperson changes what the AI recommended
  * CallBrief         - pre-call brief (with a "reviewed by" acknowledgement)
  * ConversationDraft - AI-drafted post-call notes, written to the CRM only after approval
  * SISService / SalesAssistantSettings - what SIS sells and how to judge fit (admin maintained)
"""
import re

from django.conf import settings
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL

# ── fact vocabulary ───────────────────────────────────────────────────────────
FACT_KEYS = [
    ('industry', 'Industry / business type'),
    ('need', 'Customer need'),
    ('requirement', 'Buyer requirement'),
    ('pain_point', 'Main business problem'),
    ('scale', 'Scale / size of operation'),
    ('decision_role', 'Decision-making role'),
    ('decision_process', 'Decision process'),
    ('budget', 'Budget'),
    ('timeline', 'Buying timeline'),
    ('objection', 'Objection / concern'),
    ('open_question', 'Question from customer'),
    ('competitor', 'Competitor / alternative'),
    ('current_solution', 'Current solution'),
    ('other', 'Other'),
]
FACT_KEY_LABELS = dict(FACT_KEYS)

VERIFIED, ASSUMED, REJECTED = 'verified', 'assumed', 'rejected'
FACT_STATUS_CHOICES = [(VERIFIED, 'Verified'), (ASSUMED, 'Assumption'), (REJECTED, 'Rejected')]

TIMELINE_BUCKETS = [
    ('immediate', 'Within ~30 days'),
    ('near', 'Within ~90 days'),
    ('medium', 'Within ~6 months'),
    ('long', 'Later than 6 months'),
    ('researching', 'Still researching / no date'),
]
BUCKET_DAYS = {'immediate': 30, 'near': 90, 'medium': 180, 'long': 365, 'researching': 999}
ROLE_CHOICES = [
    ('decision_maker', 'Decision maker'),
    ('influencer', 'Influencer / recommender'),
    ('user', 'End user'),
    ('gatekeeper', 'Gatekeeper / no authority'),
]

ACTION_TYPES = [
    ('demo', 'Demo'),
    ('discovery_meeting', 'Discovery Meeting'),
    ('followup_info', 'Follow-up Information'),
    ('nurture', 'Nurture'),
    ('human_review', 'Human Review'),
    ('meeting_demo', 'Book meeting / demo'),
    ('send_info', 'Send focused information / case example'),
    ('ask_question', 'Ask one precise question'),
    ('follow_up', 'Follow up'),
    ('prepare_meeting', 'Prepare for scheduled meeting'),
    ('send_proposal', 'Send / discuss proposal'),
    ('close_out_of_market', 'Record reason and close politely'),
    ('review_first', 'Review enquiry / run AI analysis first'),
    ('other', 'Other'),
]
ACTION_TYPE_LABELS = dict(ACTION_TYPES)

# the five outcomes of offer matching (what the Sales Lead chooses between)
OFFER_ACTIONS = ['demo', 'discovery_meeting', 'followup_info', 'nurture', 'human_review']

EFFORT_CHOICES = [('unknown', 'Not set'), ('low', 'Low'), ('medium', 'Medium'), ('high', 'High')]


def normalize_text(value: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', (value or '').lower())).strip()[:250]


class SalesAssistantSettings(models.Model):
    """Single row. Everything the assistant needs to know about SIS's own business."""
    ai_enabled = models.BooleanField(default=True, help_text='Master switch for AI calls (rules still run).')
    auto_analyze_new_leads = models.BooleanField(
        default=True, help_text='Review a new lead automatically when it enters the CRM.')

    target_countries = models.TextField(
        blank=True, help_text='Countries SIS serves, one per line. Empty = market fit is not judged.')
    target_industries = models.TextField(
        blank=True, help_text='Industries SIS serves, one per line (e.g. education, healthcare). Empty = not judged.')

    near_term_days = models.PositiveIntegerField(
        default=90, help_text='A decision within this many days counts as "near-term".')
    followup_days_researching = models.PositiveIntegerField(
        default=7, help_text='Follow-up interval when the customer is still researching.')
    question_wait_days = models.PositiveIntegerField(
        default=3, help_text='Days to wait for an answer before suggesting a follow-up on an unanswered question.')
    ask_budget_after_engagement = models.BooleanField(
        default=True, help_text='Only suggest a budget question once need and timeline are known and the customer has replied.')

    high_priority_score = models.PositiveSmallIntegerField(default=70)
    medium_priority_score = models.PositiveSmallIntegerField(default=40)
    min_known_factors = models.PositiveSmallIntegerField(
        default=3, help_text='Priority is only recommended when at least this many of the 6 factors are known.')
    weight_need = models.PositiveSmallIntegerField('Weight: strength of need', default=25)
    weight_readiness = models.PositiveSmallIntegerField('Weight: buying readiness', default=20)
    weight_authority = models.PositiveSmallIntegerField('Weight: decision-maker access', default=15)
    weight_deal_size = models.PositiveSmallIntegerField('Weight: expected deal size', default=15)
    weight_effort = models.PositiveSmallIntegerField('Weight: delivery effort (lower is better)', default=10)
    weight_fit = models.PositiveSmallIntegerField('Weight: fit with SIS services', default=15)

    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'AI Sales Assistant settings'
        verbose_name_plural = 'AI Sales Assistant settings'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @staticmethod
    def _lines(text):
        return [normalize_text(x) for x in re.split(r'[\n,;]+', text or '') if x.strip()]

    def countries(self):
        return self._lines(self.target_countries)

    def industries(self):
        return self._lines(self.target_industries)


class SISService(models.Model):
    """
    Approved Product / Solution Knowledge Base entry (EduPilot, SIS AI CRM, AI BDO, Custom Software ...).

    RULE: the assistant may only recommend entries with status "approved", and it may only show text that is
    written in the fields below. It never writes or completes product features, pricing, guarantees or timelines.
    An empty field is shown as "not in the knowledge base", never filled in.
    """
    KINDS = [('product', 'Product'), ('service', 'Service'),
             ('custom', 'Custom development (offered as Custom Software Discovery)')]
    STATUS = [('draft', 'Draft - not used for matching'), ('approved', 'Approved')]

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=KINDS, default='product')
    status = models.CharField(max_length=10, choices=STATUS, default='draft', db_index=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)

    # matching hints (words / phrases - editable, not customer-facing claims)
    keywords = models.TextField(
        blank=True, help_text='Problems / needs this offer solves, as words or phrases the customer uses '
                              '(one per line or comma separated). A verified need must contain one of them.')
    exclude_keywords = models.TextField(
        blank=True, help_text='If a verified need contains one of these, this offer is NOT matched.')
    industries = models.TextField(
        blank=True, help_text='Industries this offer is for (one per line, e.g. education). Empty = any industry.')

    # approved customer-facing content - the ONLY text the assistant may show about the offer
    description = models.TextField(blank=True, help_text='Approved one-paragraph summary.')
    features = models.TextField(blank=True, help_text='Approved features / capabilities, one per line.')
    pricing_note = models.TextField(blank=True, help_text='Approved pricing statement. Leave empty if pricing is quoted per project.')
    timeline_note = models.TextField(blank=True, help_text='Approved delivery timeline statement. Leave empty if not fixed.')
    guarantees_note = models.TextField(blank=True, help_text='Approved guarantees / SLA statement. Leave empty if none are offered.')
    case_examples = models.TextField(blank=True, help_text='Approved case examples / material that may be sent, one per line.')
    common_objections = models.TextField(blank=True, help_text='Known objections and approved answers, one per line.')
    discovery_questions = models.TextField(blank=True, help_text='Approved questions for a discovery meeting, one per line.')
    demo_available = models.BooleanField(default=False, help_text='A standard demo exists for this offer.')
    effort = models.CharField(max_length=10, choices=EFFORT_CHOICES, default='unknown',
                              help_text='Typical delivery effort (used for the sales-priority factor).')

    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['kind', 'name']
        verbose_name = 'Product / solution'

    def __str__(self):
        return self.name

    @property
    def is_approved(self):
        return self.status == 'approved' and self.active

    @property
    def offer_label(self):
        return 'Custom Software Discovery' if self.kind == 'custom' else self.name

    @staticmethod
    def _split(text):
        return [x.strip() for x in re.split(r'[\n]+', text or '') if x.strip()]

    def keyword_list(self):
        return [k for k in (normalize_text(x) for x in re.split(r'[\n,;]+', self.keywords or '')) if k]

    def exclude_list(self):
        return [k for k in (normalize_text(x) for x in re.split(r'[\n,;]+', self.exclude_keywords or '')) if k]

    def industry_list(self):
        return [k for k in (normalize_text(x) for x in re.split(r'[\n,;]+', self.industries or '')) if k]

    def feature_list(self):
        return self._split(self.features)

    def case_list(self):
        return self._split(self.case_examples)

    def objection_list(self):
        return self._split(self.common_objections)

    def discovery_question_list(self):
        return self._split(self.discovery_questions)

    CONTENT_FIELDS = ('name', 'kind', 'keywords', 'exclude_keywords', 'industries', 'description', 'features',
                      'pricing_note', 'timeline_note', 'guarantees_note', 'case_examples', 'common_objections',
                      'discovery_questions', 'demo_available', 'effort')


class CustomerFact(models.Model):
    """One thing we know (or assume) about a customer, with the evidence it came from."""
    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='sales_facts')
    key = models.CharField(max_length=20, choices=FACT_KEYS)
    value = models.CharField(max_length=400)
    norm = models.CharField(max_length=250, db_index=True, blank=True)
    status = models.CharField(max_length=10, choices=FACT_STATUS_CHOICES, default=ASSUMED, db_index=True)
    extra = models.JSONField(default=dict, blank=True)          # e.g. {"bucket": "near"} / {"role": "decision_maker"}
    origin = models.CharField(max_length=20, default='ai')      # ai | manual | post_call
    speaker = models.CharField(max_length=20, blank=True)       # customer | staff | unspecified
    source_ref = models.CharField(max_length=60, blank=True)    # comm:12, meeting:3 ...
    source_label = models.CharField(max_length=200, blank=True)
    evidence = models.CharField(max_length=400, blank=True)
    evidence_valid = models.BooleanField(default=False)
    note = models.CharField(max_length=300, blank=True)         # why it is only an assumption, etc.
    resolved = models.BooleanField(default=False)               # open_question answered
    confirmed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['key', '-created_at']
        indexes = [models.Index(fields=['lead', 'key', 'status'])]

    def __str__(self):
        return f'{self.key}: {self.value[:40]} [{self.status}]'

    def save(self, *args, **kwargs):
        self.norm = normalize_text(self.value)
        super().save(*args, **kwargs)

    @property
    def is_verified(self):
        return self.status == VERIFIED

    @property
    def verified_how(self):
        if self.confirmed_by_id:
            return 'confirmed by salesperson'
        if self.evidence_valid:
            return 'quoted from CRM record'
        return ''


class LeadInsight(models.Model):
    lead = models.OneToOneField('leads.Lead', on_delete=models.CASCADE, related_name='sales_insight')
    state = models.CharField(max_length=10, default='pending')   # pending | ready
    analyzed_at = models.DateTimeField(null=True, blank=True)    # last rules run
    ai_reviewed_at = models.DateTimeField(null=True, blank=True) # last successful AI extraction
    ai_error = models.CharField(max_length=300, blank=True)
    ai_fingerprint = models.CharField(max_length=64, blank=True)
    qualification = models.JSONField(default=dict, blank=True)
    recommendation = models.JSONField(default=dict, blank=True)
    offer_match = models.JSONField(default=dict, blank=True)     # product / offer matching result (rules, not AI)
    priority = models.JSONField(default=dict, blank=True)
    priority_final = models.CharField(max_length=10, blank=True)   # low | medium | high (salesperson's decision)
    priority_final_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    priority_final_at = models.DateTimeField(null=True, blank=True)
    recommendation_dismissed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Insight for lead #{self.lead_id}'


class QualificationQuestion(models.Model):
    """Tracks the one precise question we suggest, so the same question is never suggested twice."""
    STATUS = [('suggested', 'Suggested'), ('asked', 'Asked'), ('answered', 'Answered'), ('dismissed', 'Dismissed')]
    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='sales_questions')
    field = models.CharField(max_length=20)
    text = models.CharField(max_length=500)
    status = models.CharField(max_length=10, choices=STATUS, default='suggested')
    asked_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    asked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class NextAction(models.Model):
    STATUS = [('open', 'Open'), ('done', 'Done'), ('cancelled', 'Cancelled'), ('superseded', 'Replaced')]
    ORIGIN = [('ai_accepted', 'AI recommendation accepted'), ('ai_edited', 'AI recommendation changed'),
              ('human', 'Set by salesperson'), ('post_conversation', 'From approved call notes'),
              ('system', 'System')]
    lead = models.ForeignKey('leads.Lead', null=True, blank=True, on_delete=models.CASCADE, related_name='sales_next_actions')
    opportunity = models.ForeignKey('leads.Opportunity', null=True, blank=True, on_delete=models.CASCADE,
                                    related_name='sales_next_actions')
    stage_label = models.CharField('Current stage', max_length=60, blank=True)
    action_type = models.CharField(max_length=20, choices=ACTION_TYPES, default='other')
    action = models.CharField(max_length=400)
    owner = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='sales_next_actions')
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default='open', db_index=True)
    origin = models.CharField(max_length=20, choices=ORIGIN, default='human')
    ai_recommendation = models.JSONField(default=dict, blank=True)
    is_override = models.BooleanField(default=False)
    override_reason = models.CharField(max_length=400, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['due_date', '-created_at']

    def __str__(self):
        return f'{self.action[:50]} ({self.status})'

    @property
    def overdue(self):
        return self.status == 'open' and self.due_date and self.due_date < timezone.localdate()


class OverrideLog(models.Model):
    KINDS = [('next_action', 'Next action changed'), ('priority', 'Priority changed'),
             ('offer', 'Recommended product / offer changed'),
             ('recommendation_dismissed', 'Recommendation dismissed')]
    kind = models.CharField(max_length=30, choices=KINDS)
    lead = models.ForeignKey('leads.Lead', null=True, blank=True, on_delete=models.CASCADE, related_name='sales_overrides')
    opportunity = models.ForeignKey('leads.Opportunity', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    ai_value = models.JSONField(default=dict, blank=True)
    chosen_value = models.JSONField(default=dict, blank=True)
    changed_fields = models.JSONField(default=list, blank=True)
    reason = models.CharField(max_length=400, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class CallBrief(models.Model):
    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='sales_briefs')
    opportunity = models.ForeignKey('leads.Opportunity', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    content = models.JSONField(default=dict)
    ai_used = models.BooleanField(default=False)
    generated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    generated_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-generated_at']


class ConversationDraft(models.Model):
    STATUS = [('draft', 'Awaiting review'), ('approved', 'Approved'), ('discarded', 'Discarded'), ('ai_failed', 'AI unavailable')]
    COMM_TYPES = [('call', 'Phone call'), ('meeting_note', 'Meeting'), ('whatsapp', 'WhatsApp / chat')]
    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='sales_drafts')
    opportunity = models.ForeignKey('leads.Opportunity', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    meeting = models.ForeignKey('leads.Meeting', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    comm_type = models.CharField(max_length=20, choices=COMM_TYPES, default='call')
    occurred_at = models.DateTimeField(default=timezone.now)
    raw_notes = models.TextField()
    draft = models.JSONField(default=dict, blank=True)      # what the AI proposed (never edited)
    final = models.JSONField(default=dict, blank=True)      # what the salesperson approved
    status = models.CharField(max_length=12, choices=STATUS, default='draft')
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    communication_log_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

class ProposalDraft(models.Model):
    """
    AI-drafted proposal STRUCTURE for a lead that is ready to buy.

    `content` is what the assistant built (never edited): sections made only from VERIFIED facts and the APPROVED
    Product Knowledge Base, with every gap marked "[TO CONFIRM: ...]". `final` is what the salesperson approved.
    Nothing reaches the CRM Proposal list (and nothing is ever sent to the customer) until a salesperson approves.
    """
    STATUS = [('draft', 'Awaiting review'), ('approved', 'Approved'), ('discarded', 'Discarded')]
    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='sales_proposal_drafts')
    opportunity = models.ForeignKey('leads.Opportunity', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    service = models.ForeignKey(SISService, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    offer_label = models.CharField(max_length=200, blank=True)
    content = models.JSONField(default=dict, blank=True)     # what the assistant proposed (never edited)
    final = models.JSONField(default=dict, blank=True)       # what the salesperson approved
    readiness = models.JSONField(default=dict, blank=True)   # readiness check at generation time
    basis_hash = models.CharField(max_length=64, blank=True) # fingerprint of the verified facts + KB entry used
    ai_used = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=STATUS, default='draft', db_index=True)
    generated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    generated_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    crm_proposal_id = models.IntegerField(null=True, blank=True)   # leads.Proposal created on approval
    commercial_approved = models.BooleanField(
        default=False,
        help_text='Set to True only after BOTH Sales and Delivery have approved the scope snapshot.')

    class Meta:
        ordering = ['-generated_at']

    def __str__(self):
        return f'Proposal structure for lead #{self.lead_id} ({self.status})'
    
    
class OfferDecision(models.Model):
    """The Sales Lead's commercial decision on the AI's offer recommendation (accepted, or overridden with a reason)."""
    DECISIONS = [('accepted', 'Accepted AI recommendation'), ('overridden', 'Overridden by Sales Lead')]
    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='offer_decisions')
    opportunity = models.ForeignKey('leads.Opportunity', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    decision = models.CharField(max_length=12, choices=DECISIONS)
    ai_product = models.ForeignKey(SISService, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    ai_product_label = models.CharField(max_length=200, blank=True)
    ai_action_type = models.CharField(max_length=20, blank=True)
    ai_match = models.JSONField(default=dict, blank=True)          # snapshot of the AI result at decision time
    chosen_product = models.ForeignKey(SISService, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    chosen_product_label = models.CharField(max_length=200, blank=True)   # includes "No SIS offer"
    chosen_action_type = models.CharField(max_length=20, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    match_key = models.CharField(max_length=80, blank=True)
    decided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-decided_at']


class AIRun(models.Model):
    """Audit trail of every call to the language model (what kind, how many CRM records it saw, outcome)."""
    lead = models.ForeignKey('leads.Lead', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    kind = models.CharField(max_length=30)
    model = models.CharField(max_length=80, blank=True)
    sources_count = models.PositiveIntegerField(default=0)
    ok = models.BooleanField(default=False)
    error = models.CharField(max_length=300, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        
        
        
# Append to salesai/models.py

class ScopeApproval(models.Model):
    """
    Feature 1: Sales + Delivery must approve scope, features, timeline, price, assumptions and dependencies
    BEFORE any customer-facing commitment (proposal, quotation, SOW) leaves the building.
    """
    STATUS = [('pending_sales', 'Pending Sales approval'),
              ('pending_delivery', 'Pending Delivery approval'),
              ('approved', 'Approved by both'),
              ('rejected', 'Rejected'),
              ('superseded', 'Replaced')]

    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='scope_approvals')
    opportunity = models.ForeignKey('leads.Opportunity', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    proposal_draft = models.ForeignKey('salesai.ProposalDraft', null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name='scope_approvals')

    # the exact commitments being approved (snapshot, immutable once submitted)
    scope = models.JSONField(default=dict)          # {"in_scope": [], "out_of_scope": []}
    features = models.JSONField(default=list)       # approved KB feature texts
    timeline = models.JSONField(default=dict)       # {"phases": [], "go_live": "", "duration": ""}
    price = models.JSONField(default=dict)          # {"amount": "", "currency": "", "basis": ""}
    assumptions = models.JSONField(default=list)
    dependencies = models.JSONField(default=list)
    exclusions = models.JSONField(default=list)

    status = models.CharField(max_length=20, choices=STATUS, default='pending_sales', db_index=True)
    sales_approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    sales_approved_at = models.DateTimeField(null=True, blank=True)
    sales_notes = models.TextField(blank=True)
    delivery_approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    delivery_approved_at = models.DateTimeField(null=True, blank=True)
    delivery_notes = models.TextField(blank=True)
    rejection_reason = models.CharField(max_length=500, blank=True)

    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def is_fully_approved(self):
        return self.status == 'approved'

    @property
    def blocker(self):
        if self.status == 'pending_sales':
            return 'Waiting for Sales approval.'
        if self.status == 'pending_delivery':
            return 'Waiting for Delivery approval.'
        if self.status == 'rejected':
            return f'Rejected: {self.rejection_reason}'
        return ''


class ExpansionOpportunity(models.Model):
    """
    Feature 2: cross-sell / upsell / renewal / reactivation for EXISTING clients (won deals).
    Rules generate candidates; a salesperson reviews and converts one into a real opportunity.
    """
    KINDS = [('cross_sell', 'Cross-sell (another SIS product)'),
             ('upsell', 'Upsell (more users / modules / campuses)'),
             ('renewal', 'Renewal / renewal + uplift'),
             ('reactivation', 'Reactivation (dormant account)')]
    STATUS = [('suggested', 'Suggested'), ('accepted', 'Converted to opportunity'),
              ('dismissed', 'Dismissed'), ('expired', 'Expired')]

    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='expansion_opportunities')
    kind = models.CharField(max_length=15, choices=KINDS, db_index=True)
    service = models.ForeignKey('salesai.SISService', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    service_label = models.CharField(max_length=200, blank=True)
    reason = models.TextField()                       # rule-generated, cites the source
    evidence = models.JSONField(default=list)         # [{"source": "...", "text": "..."}]
    estimated_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    confidence = models.CharField(max_length=10, blank=True)   # high / medium / low
    status = models.CharField(max_length=12, choices=STATUS, default='suggested', db_index=True)
    dedup_key = models.CharField(max_length=120, blank=True, db_index=True)
    converted_opportunity = models.ForeignKey('leads.Opportunity', null=True, blank=True,
                                              on_delete=models.SET_NULL, related_name='+')
    dismissed_reason = models.CharField(max_length=400, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['lead', 'kind', 'status'])]


class FunnelEvent(models.Model):
    """
    Feature 4: Campaign -> Enquiry -> Qualified -> Meeting -> Proposal -> Sale.
    Immutable event log. Rules derive the funnel from existing CRM data + these events.
    """
    STAGES = [('campaign', 'Campaign touch'),
              ('enquiry', 'Enquiry received'),
              ('qualified', 'Qualified'),
              ('meeting', 'Meeting held'),
              ('proposal', 'Proposal sent'),
              ('sale', 'Sale (won)'),
              ('lost', 'Lost'),
              ('dormant', 'Dormant')]

    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='funnel_events')
    opportunity = models.ForeignKey('leads.Opportunity', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    stage = models.CharField(max_length=15, choices=STAGES, db_index=True)
    occurred_at = models.DateTimeField(db_index=True)
    source = models.CharField(max_length=120, blank=True)      # campaign name / platform / channel
    product_label = models.CharField(max_length=200, blank=True)
    value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-occurred_at']
        indexes = [models.Index(fields=['stage', 'occurred_at'])]


class WeeklyInsight(models.Model):
    """
    Feature 5: one row per week. AI writes the analysis; the rules confirm the numbers.
    Exactly 1-2 recommendations per week (never more) for the next sales cycle.
    """
    week_start = models.DateField(db_index=True)      # Monday
    week_end = models.DateField()

    funnel = models.JSONField(default=dict)           # counts + conversion rates per stage
    dropoffs = models.JSONField(default=list)         # [{"from": "qualified", "to": "meeting", "lost": N, "rate": "%"}]
    top_objections = models.JSONField(default=list)   # [{"label","count","lead_ids"}]
    top_lost_reasons = models.JSONField(default=list)
    product_patterns = models.JSONField(default=list) # which products convert best/worst
    message_patterns = models.JSONField(default=list)# which messaging/campaign converts best

    recommendations = models.JSONField(default=list)  # exactly 1-2: [{"title","why","action","owner_role","measure"}]
    ai_used = models.BooleanField(default=False)
    ai_error = models.CharField(max_length=300, blank=True)

    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-week_start']
        unique_together = [('week_start',)]


class SynergyForgeHandoff(models.Model):
    """
    Feature 6: on Won, hand the deal to the existing SynergyForge pipeline.
    Never auto-fires from an AI suggestion - only from a salesperson clicking "Won".
    """
    STATUS = [('pending', 'Pending handoff'), ('sent', 'Sent to SynergyForge'),
              ('failed', 'Failed'), ('acknowledged', 'Acknowledged')]

    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='synergyforge_handoffs')
    opportunity = models.ForeignKey('leads.Opportunity', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    scope_approval = models.ForeignKey('salesai.ScopeApproval', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=15, choices=STATUS, default='pending', db_index=True)
    external_ref = models.CharField(max_length=120, blank=True)
    response = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=400, blank=True)
    triggered_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
