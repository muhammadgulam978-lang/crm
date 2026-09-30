"""Thin wrapper around the Groq client the CRM already uses. Every call is logged (AIRun)."""
import json
import logging
import re
import time

from django.conf import settings

from .models import AIRun

logger = logging.getLogger('salesai')
MODEL = 'llama-3.3-70b-versatile'


class AIUnavailable(Exception):
    pass


def _parse(text: str) -> dict:
    text = (text or '').strip()
    text = re.sub(r'^```(?:json)?|```$', '', text, flags=re.M).strip()
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r'\{.*\}', text, re.S)
        if not m:
            raise AIUnavailable('The AI answer was not valid JSON.')
        try:
            data = json.loads(m.group(0))
        except ValueError as exc:
            raise AIUnavailable('The AI answer was not valid JSON.') from exc
    if not isinstance(data, dict):
        raise AIUnavailable('The AI answer was not a JSON object.')
    return data


def chat_json(system: str, payload: dict, *, kind: str, lead=None, user=None, sources_count=0,
              max_tokens=1800) -> dict:
    """Returns the parsed JSON object, or raises AIUnavailable. Never returns partial garbage."""
    started = time.time()
    run = AIRun(lead=lead, kind=kind, model=MODEL, sources_count=sources_count,
                user=user if getattr(user, 'is_authenticated', False) else None)
    try:
        key = getattr(settings, 'GROQ_API_KEY', '')
        if not key:
            raise AIUnavailable('GROQ_API_KEY is not configured.')
        from groq import Groq
        client = Groq(api_key=key, timeout=40)
        resp = client.chat.completions.create(
            model=MODEL, temperature=0, max_tokens=max_tokens,
            response_format={'type': 'json_object'},
            messages=[{'role': 'system', 'content': system},
                      {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, default=str)}])
        data = _parse(resp.choices[0].message.content)
        run.ok = True
        return data
    except AIUnavailable as exc:
        run.error = str(exc)[:300]
        raise
    except Exception as exc:  # network, quota, malformed response ...
        logger.warning('AI call failed (%s): %s', kind, exc)
        run.error = f'{type(exc).__name__}: {exc}'[:300]
        raise AIUnavailable('The AI service is not reachable right now.') from exc
    finally:
        run.duration_ms = int((time.time() - started) * 1000)
        try:
            run.save()
        except Exception:
            logger.debug('could not save AIRun', exc_info=True)
