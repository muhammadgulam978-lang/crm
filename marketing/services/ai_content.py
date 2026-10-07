"""
AI content generation with strict guardrails.

Rules enforced here, not just in the prompt:
- Only SISService entries with status='approved' are included as ground truth.
- Only salesai models that already exist are read.
- The output is a draft; human approval is a separate step.
- All calls are logged to salesai.AIRun.
"""
import json
import logging
import time

import requests
from django.conf import settings
from django.utils import timezone

from salesai.models import SISService, AIRun

logger = logging.getLogger(__name__)

GROQ_URL = 'https://api.groq.com/openai/v1/chat/completions'
DEFAULT_MODEL = 'llama-3.3-70b-versatile'
TIMEOUT = 30

SYSTEM_PROMPT = """You are a careful marketing copywriter for a software services company called "{company}".

You MUST obey these rules at all times:
1. Use ONLY the company data provided below. Do not use outside knowledge.
2. NEVER invent clients, testimonials, certifications, awards, revenue, project results, pricing, discounts, guarantees, delivery dates, or contract terms.
3. If a fact is not in the provided data, write it as "[TO CONFIRM: <what you need>]".
4. Never name a real or fictional customer.
5. Never claim a specific number of users, revenue, or years of experience.
6. Keep the tone professional, direct, and honest.
7. Do not exaggerate. Do not use superlatives unless they are in the provided data.
8. Output MUST be valid JSON matching the requested schema. No prose outside the JSON.

Approved company facts:
{company_facts}
"""

USER_TEMPLATE = """Generate a {content_kind} for the following brief.

Brief:
- Objective: {objective}
- Target industry: {industry}
- Target geography: {geography}
- Customer problem: {problem}
- Channel: {channel}
- Tone: {tone}
- Call to action: {cta}
- Campaign period: {period}

Output schema (JSON):
{schema}

Return only the JSON."""


SCHEMAS = {
    'linkedin_post':  '{"title": "...", "body": "...", "hashtags": ["...", "..."]}',
    'facebook_post':  '{"title": "...", "body": "..."}',
    'instagram_post': '{"title": "...", "caption": "...", "hashtags": ["...", "..."]}',
    'email':          '{"subject": "...", "body": "..."}',
    'followup_email': '{"subject": "...", "body": "..."}',
    'whatsapp':       '{"body": "..."}',
    'call_script':    '{"opening": "...", "questions": ["...", "..."], "objections": ["...", "..."], "closing": "..."}',
    'demo_invite':    '{"subject": "...", "body": "..."}',
    'referral_request': '{"subject": "...", "body": "..."}',
    'case_study_outline': '{"title": "...", "sections": ["...", "..."]}',
    'weekly_calendar': '{"days": [{"day": "Monday", "channel": "...", "title": "...", "hook": "..."}]}',
}


def _company_facts(service_ids=None):
    """Pull ONLY approved SISService entries. Optionally filter to selected IDs."""
    qs = SISService.objects.filter(status='approved', active=True)
    if service_ids:
        qs = qs.filter(pk__in=service_ids)
    facts = []
    for s in qs:
        facts.append({
            'name': s.name,
            'kind': s.get_kind_display(),
            'description': s.description or '[TO CONFIRM: description]',
            'features': s.feature_list(),
            'pricing': s.pricing_note or '[TO CONFIRM: pricing]',
            'timeline': s.timeline_note or '[TO CONFIRM: timeline]',
            'guarantees': s.guarantees_note or '[TO CONFIRM: guarantees]',
            'case_examples': s.case_list(),
            'effort': s.get_effort_display(),
        })
    return facts


def _call_groq(system, user):
    key = getattr(settings, 'GROQ_API_KEY', '') or ''
    if not key:
        raise RuntimeError('GROQ_API_KEY is not configured.')
    r = requests.post(
        GROQ_URL,
        headers={
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
        },
        json={
            'model': DEFAULT_MODEL,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ],
            'temperature': 0.4,
            'response_format': {'type': 'json_object'},
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def generate(*, content_kind, objective, industry='', geography='', problem='',
             channel='', tone='professional', cta='', period='',
             service_ids=None, user=None, lead=None):
    """
    Returns dict: {ok, title, subject, body, hashtags, raw, error, ai_run_id}
    Also writes a salesai.AIRun row.
    """
    if content_kind not in SCHEMAS:
        raise ValueError(f'Unsupported content kind: {content_kind}')

    company = getattr(settings, 'COMPANY_NAME', 'SIS') or 'SIS'
    facts = _company_facts(service_ids)
    facts_json = json.dumps(facts, indent=2, ensure_ascii=False, default=str)

    system = SYSTEM_PROMPT.format(company=company, company_facts=facts_json)
    user = USER_TEMPLATE.format(
        content_kind=content_kind, objective=objective, industry=industry,
        geography=geography, problem=problem, channel=channel,
        tone=tone, cta=cta, period=period, schema=SCHEMAS[content_kind],
    )

    started = time.monotonic()
    run = AIRun.objects.create(
        lead=lead, kind=f'marketing_{content_kind}', model=DEFAULT_MODEL,
        sources_count=len(facts), user=user, ok=False,
    )
    try:
        data = _call_groq(system, user)
        content = data['choices'][0]['message']['content']
        parsed = json.loads(content)
        run.ok = True
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.save(update_fields=['ok', 'duration_ms'])
        return {
            'ok': True,
            'title': parsed.get('title', ''),
            'subject': parsed.get('subject', ''),
            'body': parsed.get('body') or parsed.get('caption') or '',
            'hashtags': parsed.get('hashtags', []),
            'raw': parsed,
            'ai_run_id': run.pk,
        }
    except Exception as exc:
        logger.exception('AI generation failed for %s', content_kind)
        run.error = str(exc)[:300]
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.save(update_fields=['error', 'duration_ms'])
        return {'ok': False, 'error': str(exc), 'ai_run_id': run.pk}