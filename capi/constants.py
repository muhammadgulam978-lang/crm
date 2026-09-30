PLATFORM_META = 'meta'
PLATFORM_LINKEDIN = 'linkedin'
PLATFORM_CHOICES = [
    (PLATFORM_META, 'Meta (Conversions API)'),
    (PLATFORM_LINKEDIN, 'LinkedIn (Conversions API)'),
]

# Where a lead originally came from (attribution platform)
TRACKING_PLATFORM_CHOICES = [
    ('', 'Unknown / not from ads'),
    ('meta', 'Meta'),
    ('linkedin', 'LinkedIn'),
    ('google', 'Google'),
    ('other', 'Other'),
]

CONSENT_GRANTED = 'granted'
CONSENT_DENIED = 'denied'
CONSENT_UNKNOWN = 'unknown'
CONSENT_CHOICES = [
    (CONSENT_GRANTED, 'Granted'),
    (CONSENT_DENIED, 'Denied'),
    (CONSENT_UNKNOWN, 'Unknown'),
]

# ---- CRM triggers an administrator can attach a conversion event to ----
TRIGGER_LEAD_CREATED = 'lead_created'
TRIGGER_LEAD_STATUS = 'lead_status_changed'
TRIGGER_LEAD_SCORE = 'lead_score_reached'
TRIGGER_OPP_STAGE = 'opportunity_stage_changed'
TRIGGER_MEETING = 'meeting_booked'
TRIGGER_QUOTE_APPROVED = 'quotation_approved'
TRIGGER_CONTRACT_SIGNED = 'contract_signed'
TRIGGER_DEAL_WON = 'deal_won'
TRIGGER_REVENUE = 'revenue_recorded'

TRIGGER_CHOICES = [
    (TRIGGER_LEAD_CREATED, 'Lead created'),
    (TRIGGER_LEAD_STATUS, 'Lead status changed (use From / To)'),
    (TRIGGER_LEAD_SCORE, 'AI lead score reaches threshold (use Min score)'),
    (TRIGGER_OPP_STAGE, 'Opportunity stage changed (use From / To)'),
    (TRIGGER_MEETING, 'Appointment / meeting booked'),
    (TRIGGER_QUOTE_APPROVED, 'Proposal (quotation) accepted'),
    (TRIGGER_CONTRACT_SIGNED, 'Contract signed'),
    (TRIGGER_DEAL_WON, 'Deal marked won (opportunity Won or lead Converted)'),
    (TRIGGER_REVENUE, 'Revenue recorded (payment added)'),
]

# Triggers whose event carries a monetary value by nature
VALUE_TRIGGERS = {TRIGGER_QUOTE_APPROVED, TRIGGER_CONTRACT_SIGNED,
                  TRIGGER_DEAL_WON, TRIGGER_REVENUE}

# ---- Event status ----
ST_PENDING = 'pending'
ST_PROCESSING = 'processing'
ST_SENT = 'sent'
ST_RETRYING = 'retrying'
ST_FAILED = 'failed'
ST_SKIPPED = 'skipped'
ST_CANCELLED = 'cancelled'
STATUS_CHOICES = [
    (ST_PENDING, 'Pending'),
    (ST_PROCESSING, 'Processing'),
    (ST_SENT, 'Sent'),
    (ST_RETRYING, 'Retrying'),
    (ST_FAILED, 'Failed'),
    (ST_SKIPPED, 'Skipped'),
    (ST_CANCELLED, 'Cancelled'),
]

# Seconds to wait before attempt N+1 (attempt 1 failed -> wait 60s, ...)
RETRY_BACKOFF = [60, 300, 1800, 7200, 43200]
MAX_ATTEMPTS = 5
META_MAX_EVENT_AGE_DAYS = 7
