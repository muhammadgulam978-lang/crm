"""Marketing Center enumerations. Kept in one place so migrations stay stable."""

# ---- Prospect source kinds ----
SOURCE_KIND_REFERRAL = 'referral'
SOURCE_KIND_EVENT = 'event'
SOURCE_KIND_ORGANIC = 'organic'
SOURCE_KIND_PARTNER = 'partner'
SOURCE_KIND_WEBINAR = 'webinar'
SOURCE_KIND_COLD_LIST = 'cold_list'
SOURCE_KIND_INBOUND_FORM = 'inbound_form'
SOURCE_KIND_SOCIAL = 'social'
SOURCE_KIND_OTHER = 'other'
SOURCE_KIND_CHOICES = [
    (SOURCE_KIND_REFERRAL, 'Referral'),
    (SOURCE_KIND_EVENT, 'Event / Conference'),
    (SOURCE_KIND_ORGANIC, 'Organic / SEO'),
    (SOURCE_KIND_PARTNER, 'Partner'),
    (SOURCE_KIND_WEBINAR, 'Webinar'),
    (SOURCE_KIND_COLD_LIST, 'Cold list / purchased data'),
    (SOURCE_KIND_INBOUND_FORM, 'Inbound form'),
    (SOURCE_KIND_SOCIAL, 'Social (non-paid)'),
    (SOURCE_KIND_OTHER, 'Other'),
]

# ---- Campaign ----
CAMPAIGN_STATUS_DRAFT = 'draft'
CAMPAIGN_STATUS_SCHEDULED = 'scheduled'
CAMPAIGN_STATUS_ACTIVE = 'active'
CAMPAIGN_STATUS_PAUSED = 'paused'
CAMPAIGN_STATUS_COMPLETED = 'completed'
CAMPAIGN_STATUS_CANCELLED = 'cancelled'
CAMPAIGN_STATUS_CHOICES = [
    (CAMPAIGN_STATUS_DRAFT, 'Draft'),
    (CAMPAIGN_STATUS_SCHEDULED, 'Scheduled'),
    (CAMPAIGN_STATUS_ACTIVE, 'Active'),
    (CAMPAIGN_STATUS_PAUSED, 'Paused'),
    (CAMPAIGN_STATUS_COMPLETED, 'Completed'),
    (CAMPAIGN_STATUS_CANCELLED, 'Cancelled'),
]

CAMPAIGN_CHANNEL_EMAIL = 'email'
CAMPAIGN_CHANNEL_SMS = 'sms'
CAMPAIGN_CHANNEL_WHATSAPP = 'whatsapp'
CAMPAIGN_CHANNEL_CALL = 'call'
CAMPAIGN_CHANNEL_SOCIAL = 'social'
CAMPAIGN_CHANNEL_MIXED = 'mixed'          # ← the missing one
CAMPAIGN_CHANNEL_CHOICES = [
    (CAMPAIGN_CHANNEL_EMAIL, 'Email'),
    (CAMPAIGN_CHANNEL_SMS, 'SMS'),
    (CAMPAIGN_CHANNEL_WHATSAPP, 'WhatsApp'),
    (CAMPAIGN_CHANNEL_CALL, 'Phone call'),
    (CAMPAIGN_CHANNEL_SOCIAL, 'Social'),
    (CAMPAIGN_CHANNEL_MIXED, 'Mixed'),
]

# ---- Marketing content ----
CONTENT_KIND_EMAIL = 'email'
CONTENT_KIND_SMS = 'sms'
CONTENT_KIND_WHATSAPP = 'whatsapp'
CONTENT_KIND_CALL_SCRIPT = 'call_script'
CONTENT_KIND_SOCIAL_POST = 'social_post'
CONTENT_KIND_LANDING = 'landing'
CONTENT_KIND_CHOICES = [
    (CONTENT_KIND_EMAIL, 'Email'),
    (CONTENT_KIND_SMS, 'SMS'),
    (CONTENT_KIND_WHATSAPP, 'WhatsApp'),
    (CONTENT_KIND_CALL_SCRIPT, 'Call script'),
    (CONTENT_KIND_SOCIAL_POST, 'Social post'),
    (CONTENT_KIND_LANDING, 'Landing page'),
]

# ---- Consent (channel x purpose) ----
CHANNEL_EMAIL = 'email'
CHANNEL_SMS = 'sms'
CHANNEL_WHATSAPP = 'whatsapp'
CHANNEL_CALL = 'call'
CHANNEL_CHOICES = [
    (CHANNEL_EMAIL, 'Email'),
    (CHANNEL_SMS, 'SMS'),
    (CHANNEL_WHATSAPP, 'WhatsApp'),
    (CHANNEL_CALL, 'Phone call'),
]

PURPOSE_MARKETING = 'marketing'
PURPOSE_TRANSACTIONAL = 'transactional'
PURPOSE_PROFILING = 'profiling'
PURPOSE_CHOICES = [
    (PURPOSE_MARKETING, 'Marketing'),
    (PURPOSE_TRANSACTIONAL, 'Transactional'),
    (PURPOSE_PROFILING, 'Profiling / analytics'),
]

CONSENT_GRANTED = 'granted'
CONSENT_DENIED = 'denied'
CONSENT_WITHDRAWN = 'withdrawn'
CONSENT_UNKNOWN = 'unknown'
CONSENT_STATUS_CHOICES = [
    (CONSENT_GRANTED, 'Granted'),
    (CONSENT_DENIED, 'Denied'),
    (CONSENT_WITHDRAWN, 'Withdrawn'),
    (CONSENT_UNKNOWN, 'Unknown'),
]

CONSENT_SOURCE_WEB_FORM = 'web_form'
CONSENT_SOURCE_IMPORT = 'import'
CONSENT_SOURCE_VERBAL = 'verbal'
CONSENT_SOURCE_CONTRACT = 'contract'
CONSENT_SOURCE_LEADTRACKING = 'capi_lead_tracking'
CONSENT_SOURCE_CHOICES = [
    (CONSENT_SOURCE_WEB_FORM, 'Web form'),
    (CONSENT_SOURCE_IMPORT, 'Import'),
    (CONSENT_SOURCE_VERBAL, 'Verbal'),
    (CONSENT_SOURCE_CONTRACT, 'Contract'),
    (CONSENT_SOURCE_LEADTRACKING, 'Ad-platform tracking'),
]

# ---- Suppression ----
SUPPRESSION_REASON_UNSUBSCRIBE = 'unsubscribe'
SUPPRESSION_REASON_BOUNCE = 'bounce'
SUPPRESSION_REASON_COMPLAINT = 'complaint'
SUPPRESSION_REASON_MANUAL = 'manual'
SUPPRESSION_REASON_LEGAL = 'legal'
SUPPRESSION_REASON_CHOICES = [
    (SUPPRESSION_REASON_UNSUBSCRIBE, 'Unsubscribed'),
    (SUPPRESSION_REASON_BOUNCE, 'Hard bounce'),
    (SUPPRESSION_REASON_COMPLAINT, 'Spam complaint'),
    (SUPPRESSION_REASON_MANUAL, 'Manually suppressed'),
    (SUPPRESSION_REASON_LEGAL, 'Legal / regulatory'),
]

# ---- Automation ----
AUTOMATION_STATUS_PENDING = 'pending'
AUTOMATION_STATUS_RUNNING = 'running'
AUTOMATION_STATUS_DONE = 'done'
AUTOMATION_STATUS_FAILED = 'failed'
AUTOMATION_STATUS_CANCELLED = 'cancelled'
AUTOMATION_STATUS_CHOICES = [
    (AUTOMATION_STATUS_PENDING, 'Pending'),
    (AUTOMATION_STATUS_RUNNING, 'Running'),
    (AUTOMATION_STATUS_DONE, 'Done'),
    (AUTOMATION_STATUS_FAILED, 'Failed'),
    (AUTOMATION_STATUS_CANCELLED, 'Cancelled'),
]

STEP_KIND_WAIT = 'wait'
STEP_KIND_SEND_EMAIL = 'send_email'
STEP_KIND_SEND_SMS = 'send_sms'
STEP_KIND_SEND_WHATSAPP = 'send_whatsapp'
STEP_KIND_CREATE_TASK = 'create_task'
STEP_KIND_CREATE_FOLLOWUP = 'create_followup'
STEP_KIND_NOTIFY = 'notify'
STEP_KIND_CHOICES = [
    (STEP_KIND_WAIT, 'Wait'),
    (STEP_KIND_SEND_EMAIL, 'Send email'),
    (STEP_KIND_SEND_SMS, 'Send SMS'),
    (STEP_KIND_SEND_WHATSAPP, 'Send WhatsApp'),
    (STEP_KIND_CREATE_TASK, 'Create general task'),
    (STEP_KIND_CREATE_FOLLOWUP, 'Create follow-up'),
    (STEP_KIND_NOTIFY, 'Notify user'),
]

# ---- Referral ----
REFERRAL_STATUS_PENDING = 'pending'
REFERRAL_STATUS_QUALIFIED = 'qualified'
REFERRAL_STATUS_CONVERTED = 'converted'
REFERRAL_STATUS_REWARDED = 'rewarded'
REFERRAL_STATUS_REJECTED = 'rejected'
REFERRAL_STATUS_CHOICES = [
    (REFERRAL_STATUS_PENDING, 'Pending'),
    (REFERRAL_STATUS_QUALIFIED, 'Qualified'),
    (REFERRAL_STATUS_CONVERTED, 'Converted'),
    (REFERRAL_STATUS_REWARDED, 'Rewarded'),
    (REFERRAL_STATUS_REJECTED, 'Rejected'),
]

REFERRER_KIND_LEAD = 'lead'
REFERRER_KIND_CONTACT = 'contact'
REFERRER_KIND_COMPANY = 'company'
REFERRER_KIND_STAFF = 'staff'
REFERRER_KIND_PARTNER = 'partner'
REFERRER_KIND_CHOICES = [
    (REFERRER_KIND_LEAD, 'Existing lead'),
    (REFERRER_KIND_CONTACT, 'Contact'),
    (REFERRER_KIND_COMPANY, 'Company'),
    (REFERRER_KIND_STAFF, 'Staff member'),
    (REFERRER_KIND_PARTNER, 'Partner'),
]

MAX_AUTOMATION_ATTEMPTS = 5