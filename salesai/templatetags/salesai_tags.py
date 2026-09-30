from django import template
from django.contrib.auth import get_user_model

from salesai import services
from salesai.models import ACTION_TYPES, OFFER_ACTIONS, LeadInsight, NextAction, SISService

register = template.Library()


@register.inclusion_tag('salesai/_panel.html', takes_context=True)
def salesai_panel(context, lead, next_url=''):
    """
    "AI Product Match" card for the Lead / Opportunity screens. Read-only: it never calls the AI and never
    changes data; accept / override are POSTs to the assistant.
    """
    request = context.get('request')
    insight = LeadInsight.objects.filter(lead=lead).first() if lead else None
    rec = (insight.recommendation if insight else {}) or {}
    match = (insight.offer_match if insight else {}) or {}
    q = (insight.qualification if insight else {}) or {}
    dec = services.latest_decision(lead) if lead else None
    missing = []
    for k in q.get('missing', []):
        f = q['fields'][k]
        if k == 'budget' and rec.get('code') in ('DEMO', 'DISCOVERY', 'MATCHED', 'CUSTOM_DISCOVERY', 'AMBIGUOUS_DISCOVERY',
                                                 'INFO_FOLLOWUP', 'NURTURE'):
            continue                       # budget is not needed to choose the next step
        missing.append({'label': f['label'], 'assumed': f['value'] if f['status'] == 'assumed' else ''})
    if match.get('needs_industry') and q.get('fields', {}).get('industry', {}).get('status') != 'verified':
        missing.append({'label': 'Industry / type of organisation', 'assumed': ''})
    return {
        'lead': lead, 'insight': insight, 'rec': rec, 'match': match, 'missing': missing,
        'question': rec.get('question'), 'prio': (insight.priority if insight else {}) or {},
        'next_action': NextAction.objects.filter(lead=lead, status='open').select_related('owner').first() if lead else None,
        'decision': dec,
        'decision_stale': bool(dec and insight and dec.match_key != services.offer_match_key(insight)),
        'approved_offers': SISService.objects.filter(status='approved', active=True),
        'offer_actions': [(a, dict(ACTION_TYPES)[a]) for a in OFFER_ACTIONS],
        'users': get_user_model().objects.filter(is_active=True).order_by('username'),
        'default_owner': services.default_owner(lead) if lead else None,
        'request': request, 'csrf_token': context.get('csrf_token'), 'next_url': next_url or (request.path if request else ''),
    }
