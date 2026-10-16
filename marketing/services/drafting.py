"""
AI message drafting for follow-ups.

Uses OpenAI (see settings.OPENAI_*). If OPENAI_API_KEY not set, returns
a template-based draft instead — automation never blocks on AI availability.
"""
import json
import logging

from django.conf import settings
from django.utils import timezone

from leads.models import Lead
from marketing import constants as C
from marketing.models import LeadStage
from marketing.services import qualify

logger = logging.getLogger(__name__)

# Template fallbacks (used when AI is not configured)
TEMPLATES = {
    'initial_outreach': (
        'Hi {name},\n\n'
        'We noticed {company_clause}might benefit from our {service}. '
        'Would you be open to a short call this week?\n\n'
        'Best,\n{sender_name}'
    ),
    'follow_up': (
        'Hi {name},\n\n'
        'Just following up on my previous message. '
        'I would love to share how we help businesses like yours.\n\n'
        'Best,\n{sender_name}'
    ),
    'meeting_follow_up': (
        'Hi {name},\n\n'
        'Thanks for your time. As discussed, here is what we can do...\n\n'
        'Best,\n{sender_name}'
    ),
    'proposal_follow_up': (
        'Hi {name},\n\n'
        'Following up on the proposal we sent. Do you have any questions '
        'or want to discuss next steps?\n\n'
        'Best,\n{sender_name}'
    ),
    're_engagement': (
        'Hi {name},\n\n'
        'It has been a while. We have some new updates that might interest you.\n\n'
        'Best,\n{sender_name}'
    ),
}


def _sender_name(lead):
    if lead.spo and lead.spo.name:
        return lead.spo.name
    return getattr(settings, 'COMPANY_NAME', 'Our team')


def _company_clause(lead):
    return ''  # placeholder — could use Lead.detail company later


def draft_from_template(lead, kind, **extra):
    """Fallback template draft. Always succeeds."""
    tpl = TEMPLATES.get(kind, TEMPLATES['follow_up'])
    body = tpl.format(
        name=lead.name or 'there',
        company_clause=_company_clause(lead),
        service=extra.get('service', 'services'),
        sender_name=_sender_name(lead),
    )
    return {
        'subject': extra.get('subject', 'Quick question'),
        'body': body,
        'source': 'template',
        'kind': kind,
    }


def draft_with_ai(lead, kind, *, objective='', tone='professional'):
    """Call OpenAI to draft a message. Returns dict or None on failure."""
    key = getattr(settings, 'OPENAI_API_KEY', '') or ''
    if not key:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logger.info('openai package not installed — using template fallback')
        return None

    try:
        client = OpenAI(
            api_key=key,
            base_url=getattr(settings, 'OPENAI_BASE_URL', 'https://api.openai.com/v1'),
            timeout=30,
        )
        stage_info = ''
        try:
            stage_obj = LeadStage.objects.filter(lead=lead).first()
            if stage_obj:
                stage_info = f'Current stage: {stage_obj.stage}'
        except Exception:
            pass

        profile = f"""
Lead name: {lead.name}
Company: {(lead.detail or '')[:200]}
Stage: {stage_info}
Assigned to: {_sender_name(lead)}
Service interest: {lead.business_requirement or 'unknown'}
"""

        prompt = (
            f'Write a {kind.replace("_", " ")} email/message for this B2B software services lead.\n\n'
            f'{profile}\n\n'
            f'Objective: {objective or "Start a conversation"}\n'
            f'Tone: {tone}\n\n'
            'Rules:\n'
            '- Do not invent pricing, guarantees, or client names\n'
            '- Keep it under 100 words\n'
            '- End with a clear next step\n\n'
            'Return JSON only: {"subject": "...", "body": "..."}'
        )

        resp = client.chat.completions.create(
            model=getattr(settings, 'OPENAI_MODEL', 'gpt-4o-mini'),
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.4,
            response_format={'type': 'json_object'},
        )
        data = json.loads(resp.choices[0].message.content or '{}')
        if not data.get('body'):
            return None
        return {
            'subject': data.get('subject', '')[:200],
            'body': data.get('body', '')[:5000],
            'source': 'ai',
            'kind': kind,
        }
    except Exception:
        logger.exception('AI drafting failed for lead %s', lead.pk)
        return None


def draft(lead, kind, *, objective='', tone='professional', **extra):
    """
    Draft a message — tries AI first, falls back to template.
    Returns dict {subject, body, source, kind}.
    """
    result = draft_with_ai(lead, kind, objective=objective, tone=tone)
    if result:
        return result
    return draft_from_template(lead, kind, **extra)