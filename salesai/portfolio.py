"""Customer understanding across ALL leads: repeated needs, common objections, unanswered questions, gaps."""
from collections import defaultdict

from django.db.models import Count

from .models import VERIFIED, CustomerFact, LeadInsight, normalize_text
from .services import service_catalogue


def _group_label(value, services):
    n = normalize_text(value)
    for s in services:
        if any(k and k in n for k in s['keywords']):
            return f'{s["name"]} (SIS service)'
    return value.strip()[:70]


def grouped(keys, status=VERIFIED, limit=10, min_leads=1):
    services = service_catalogue()
    groups = defaultdict(lambda: {'leads': set(), 'examples': []})
    for f in CustomerFact.objects.filter(key__in=keys, status=status).select_related('lead')[:3000]:
        if f.lead.is_duplicate:
            continue
        g = groups[_group_label(f.value, services) if 'need' in keys or 'requirement' in keys else normalize_text(f.value)[:70] or f.value[:70]]
        g['leads'].add(f.lead_id)
        if len(g['examples']) < 3:
            g['examples'].append({'lead_id': f.lead_id, 'lead': f.lead.name, 'text': f.value, 'source': f.source_label})
    rows = [{'label': k, 'count': len(v['leads']), 'examples': v['examples']} for k, v in groups.items()
            if len(v['leads']) >= min_leads]
    rows.sort(key=lambda r: -r['count'])
    return rows[:limit]


def common_objections(limit=3, exclude_lead=None):
    rows = grouped(['objection'], min_leads=2, limit=limit + 3)
    return [r for r in rows if not exclude_lead or all(e['lead_id'] != exclude_lead for e in r['examples'])][:limit]


def summary():
    from leads.models import Lead
    active = Lead.objects.filter(is_duplicate=False).exclude(status__in=['lost', 'converted'])
    insights = LeadInsight.objects.filter(lead__in=active)
    missing = defaultdict(int)
    for i in insights:
        for k in (i.qualification or {}).get('missing', []):
            missing[k] += 1
    unanswered = (CustomerFact.objects.filter(key='open_question', resolved=False, status=VERIFIED, lead__in=active)
                  .select_related('lead').order_by('created_at')[:25])
    campaigns = []
    try:
        from capi.models import LeadTracking
        rows = defaultdict(lambda: {'leads': 0, 'need_verified': 0, 'near_term': 0})
        need_ids = set(CustomerFact.objects.filter(key='need', status=VERIFIED).values_list('lead_id', flat=True))
        ins = {i.lead_id: i for i in insights}
        for t in LeadTracking.objects.exclude(campaign_name='').filter(lead__in=active):
            r = rows[t.campaign_name]
            r['leads'] += 1
            r['need_verified'] += t.lead_id in need_ids
            i = ins.get(t.lead_id)
            r['near_term'] += bool(i and (i.recommendation or {}).get('code') == 'MEETING_DEMO')
        campaigns = sorted(({'name': k, **v} for k, v in rows.items()), key=lambda r: -r['leads'])[:15]
    except Exception:
        pass
    assumptions = CustomerFact.objects.filter(status='assumed', lead__in=active).count()
    return {
        'needs': grouped(['need', 'requirement']),
        'objections': grouped(['objection']),
        'competitors': grouped(['competitor', 'current_solution']),
        'unanswered': unanswered,
        'missing': dict(missing),
        'active_leads': active.count(),
        'reviewed_leads': insights.exclude(ai_reviewed_at=None).count(),
        'assumptions_pending': assumptions,
        'campaigns': campaigns,
    }
