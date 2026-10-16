"""
Detect when a lead has replied or opted out.
Pauses all automation runs, logs the event, and notifies the assigned salesperson.

Idempotent: safe to call multiple times per message.
"""
import logging
import re

from django.utils import timezone

from leads.models import CommunicationLog, Lead, Notification
from marketing import constants as C
from marketing.models import AutomationRun, LeadStage

logger = logging.getLogger(__name__)

# Opt-out detection patterns (multilingual)
OPT_OUT_PATTERNS = [
    # English
    r'\b(unsubscribe|opt[\s-]?out|stop|remove me|don\'?t (contact|message|email|call))\b',
    r'\bnot interested\b',
    r'\bplease stop\b',
    # Urdu (roman)
    r'\b(mujhe|humein|hamein)\s+(message|email|call|contact)\s+(na|nahi|mat)\s+(karein|karo|bhejein)\b',
    r'\b(band|stop)\s+(kar|karo|karein|kardo|kardein)\b',
    r'\binterest\s+nahi\b',
    # Urdu (script)
    r'(رابطہ|پیغام|کال)\s*نہ\s*کریں',
    r'(دلچسپی\s*نہیں)',
    # Hindi (roman)
    r'\b(mat\s+(bhejo|karo|karein))\b',
    r'\binteres(t|ted)\s+nahi\b',
]

OPT_OUT_RE = re.compile('|'.join(OPT_OUT_PATTERNS), re.IGNORECASE)

# Positive reply detection (weak — just signals engagement, not intent)
POSITIVE_PATTERNS = [
    r'\b(yes|sure|ok|okay|interested|tell me more|share details|please share)\b',
    r'\b(haan|han|ji|zaroor|theek|thik|batao|bataein|bhejo|send karo)\b',
    r'\b(kab|when|kaise|how|kitna|how much|price|pricing|cost)\b',
]
POSITIVE_RE = re.compile('|'.join(POSITIVE_PATTERNS), re.IGNORECASE)


def classify_reply(text):
    """
    Returns one of: 'opt_out' | 'positive' | 'neutral'
    """
    if not text:
        return 'neutral'
    text = text.strip()
    if OPT_OUT_RE.search(text):
        return 'opt_out'
    if POSITIVE_RE.search(text):
        return 'positive'
    return 'neutral'


def pause_automation(lead, *, reason, stop_external=False):
    """Pause all pending/running automations for a lead. Idempotent."""
    qs = AutomationRun.objects.filter(
        lead=lead,
        status__in=[
            C.AUTOMATION_STATUS_PENDING,
            C.AUTOMATION_STATUS_RUNNING,
        ],
    )
    count = qs.count()
    if count == 0:
        return 0
    qs.update(
        status=C.AUTOMATION_STATUS_CANCELLED,
        last_error=reason[:500],
        updated_at=timezone.now(),
    )
    logger.info('Paused %s automation(s) for lead %s: %s', count, lead.pk, reason)
    return count


def notify_owner(lead, *, title, message, link=''):
    """Notify the lead's assigned salesperson. Idempotent within an hour."""
    user = None
    if lead.spo and lead.spo.user:
        user = lead.spo.user
    if user is None:
        logger.info('No SPO/user for lead %s — skipping notification', lead.pk)
        return None

    # Idempotency: don't spam same notification within 1 hour
    one_hour_ago = timezone.now() - timezone.timedelta(hours=1)
    existing = Notification.objects.filter(
        user=user,
        title=title,
        message=message,
        created_at__gte=one_hour_ago,
    ).exists()
    if existing:
        return None

    return Notification.objects.create(
        user=user,
        notif_type='general',
        title=title[:200],
        message=message[:2000],
        link=link,
    )


def handle_inbound_message(lead, text, *, source='manual'):
    """
    Main entry point. Called when an inbound message is received.
    Returns dict: {classification, paused_count, notified}
    """
    classification = classify_reply(text)

    # Always log the inbound event on the stage
    try:
        stage_obj, _ = LeadStage.objects.get_or_create(lead=lead)
    except Exception:
        stage_obj = None

    result = {'classification': classification, 'paused_count': 0, 'notified': False}

    if classification == 'opt_out':
        # 1. Pause automation
        result['paused_count'] = pause_automation(
            lead, reason=f'Opt-out detected from {source}',
        )
        # 2. Move stage
        if stage_obj:
            stage_obj.set_stage(
                C.STAGE_LOST,
                reason=f'Opted out via {source}',
            )
        # 3. Notify owner
        n = notify_owner(
            lead,
            title=f'Opt-out: {lead.name}',
            message=(
                f'Lead "{lead.name}" (ID {lead.pk}) asked to stop contact.\n'
                f'Message: {text[:500]}\n'
                f'All pending automation has been paused.'
            ),
            link=f'/leads/{lead.pk}/',
        )
        result['notified'] = bool(n)

    elif classification == 'positive':
        # Move stage if not already past "replied"
        if stage_obj and stage_obj.stage in (C.STAGE_NEW, C.STAGE_CONTACTED, C.STAGE_RESEARCHED):
            stage_obj.set_stage(
                C.STAGE_REPLIED,
                reason=f'Positive reply via {source}',
            )
        # Notify owner
        n = notify_owner(
            lead,
            title=f'Reply received: {lead.name}',
            message=(
                f'Lead "{lead.name}" (ID {lead.pk}) replied positively.\n'
                f'Message: {text[:500]}\n'
                f'Please respond within 24 hours.'
            ),
            link=f'/leads/{lead.pk}/',
        )
        result['notified'] = bool(n)
        # Pause any running automation — human takes over now
        result['paused_count'] = pause_automation(
            lead, reason=f'Positive reply from {source} — human follow-up',
        )

    return result