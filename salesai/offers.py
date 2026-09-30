"""
Offer matching: confirmed customer need  ->  approved SIS product / service  (or Custom Software Discovery).

100 % rules, no AI. The only inputs are
  * VERIFIED facts (quoted from a CRM record, or confirmed / entered by a salesperson), and
  * the APPROVED Product / Solution Knowledge Base.
Assumptions never drive the recommendation; they are only shown as "possible match if confirmed".
"""
from .models import normalize_text

NEED_KEYS = ('need', 'requirement', 'pain_point', 'scale')


def _tokens_present(keyword, padded_text):
    return f' {keyword} ' in padded_text


def _text_of(facts, statuses, keys=NEED_KEYS):
    parts = [normalize_text(f['value']) for f in facts if f['key'] in keys and f['status'] in statuses and not f.get('resolved')]
    return ' ' + ' '.join(parts) + ' ' if parts else ''


def known_industry(state, statuses=('verified',)):
    """(industry text, source label, status). Verified customer statement first, then the Company record."""
    hits = [f for f in state.facts if f['key'] == 'industry' and f['status'] in statuses]
    hits.sort(key=lambda f: (bool(f.get('confirmed')), f.get('pk', 0)), reverse=True)
    if hits:
        return normalize_text(hits[0]['value']), hits[0].get('source_label') or 'CRM record', hits[0]['status']
    if 'verified' in statuses and state.industry and normalize_text(state.industry) != 'other':
        return normalize_text(state.industry), 'Company record', 'verified'
    return '', '', ''


def _industry_ok(svc, industry):
    """True / False / None(unknown). An offer with no industry list fits any industry."""
    allowed = svc['industries']
    if not allowed:
        return True
    if not industry:
        return None
    return any(a in industry or industry in a for a in allowed)


def _score_services(state, statuses):
    text = _text_of(state.facts, statuses)
    if not text:
        return [], text
    industry, _, _ = known_industry(state, statuses)
    out = []
    for s in state.services:                      # only APPROVED, active entries are ever in state.services
        excluded = [k for k in s['exclude_keywords'] if _tokens_present(k, text)]
        hits = [k for k in s['keywords'] if _tokens_present(k, text)]
        # "automated follow up" already covers "follow up": one phrase in the need counts once per offer
        hits = [k for k in hits if not any(k != o and f' {k} ' in f' {o} ' for o in hits)]
        if excluded or not hits:
            continue
        ind = _industry_ok(s, industry)
        if ind is False:
            continue
        out.append({'id': s['id'], 'name': s['name'], 'label': s['label'], 'kind': s['kind'], 'hits': hits,
                    'score': len(hits) + (1 if ind else 0), 'industry_ok': ind, 'demo': s['demo_available']})
    out.sort(key=lambda c: (-c['score'], c['kind'] == 'custom', c['name']))
    return out, text


def _main_problem(state, statuses):
    for key in ('pain_point', 'need'):
        hits = [f for f in state.facts if f['key'] == key and f['status'] in statuses and not f.get('resolved')]
        hits.sort(key=lambda f: (bool(f.get('confirmed')), f.get('pk', 0)), reverse=True)
        if hits:
            f = hits[0]
            return {'text': f['value'], 'status': f['status'], 'source': f.get('source_label', ''), 'key': key}
    return None


def match_offers(state):
    """
    status:  no_need     no verified need yet
             matched     one clear approved product / service
             ambiguous   two or more approved products match equally well
             custom      need is verified but no standard product fits -> Custom Software Discovery
             none        need is verified, nothing in the approved KB fits and there is no custom offer
    """
    verified_problem = _main_problem(state, ('verified',))
    assumed_problem = _main_problem(state, ('assumed',)) if not verified_problem else None
    industry, ind_src, ind_status = known_industry(state)
    result = {'status': 'no_need', 'primary': None, 'candidates': [], 'why': [], 'needs_industry': False,
              'main_problem': verified_problem, 'assumed_problem': assumed_problem,
              'industry': {'value': industry, 'source': ind_src, 'status': ind_status},
              'tentative': None, 'kb_empty': not state.services}

    if not verified_problem:
        # show what WOULD match if the salesperson confirms the assumption - never as a recommendation
        cands, _ = _score_services(state, ('assumed',))
        if cands:
            top = cands[0]
            result['tentative'] = {'name': top['name'], 'label': top['label'],
                                   'because': assumed_problem['text'] if assumed_problem else ''}
        return result
    if not state.services:
        result['status'] = 'none'
        result['why'].append('The approved Product / Solution Knowledge Base is empty, so nothing can be matched. '
                             'Add and approve SIS products first.')
        return result

    cands, _ = _score_services(state, ('verified',))
    products = [c for c in cands if c['kind'] != 'custom']
    customs = [c for c in cands if c['kind'] == 'custom']
    fallback_custom = next((s for s in state.services if s['kind'] == 'custom'), None)
    need_txt = verified_problem['text']

    if products:
        top = products[0]
        tied = [c for c in products if c['score'] == top['score']]
        result['candidates'] = products
        if len(tied) == 1:
            result['status'], result['primary'] = 'matched', top
            result['why'].append(f'Verified {verified_problem["key"].replace("_", " ")} "{need_txt}" '
                                 f'({verified_problem["source"] or "CRM record"}) contains: {", ".join(top["hits"])}.')
        else:
            result['status'] = 'ambiguous'
            result['candidates'] = tied
            names = ' / '.join(c['name'] for c in tied)
            result['why'].append(f'Verified need "{need_txt}" fits more than one approved offer equally well ({names}). '
                                 f'A discovery conversation should decide which one.')
        for c in result['candidates']:
            if c['industry_ok'] is True:
                s = next(x for x in state.services if x['id'] == c['id'])
                if s['industries']:
                    result['why'].append(f'Industry "{industry}" ({ind_src}) is one of the industries approved for {c["name"]}.')
            elif c['industry_ok'] is None:
                result['needs_industry'] = True
                result['why'].append(f'{c["name"]} is approved for specific industries and the customer\'s industry is not verified yet.')
    elif customs or fallback_custom:
        cust = customs[0] if customs else {'id': fallback_custom['id'], 'name': fallback_custom['name'],
                                           'label': fallback_custom['label'], 'kind': 'custom', 'hits': [], 'score': 0,
                                           'industry_ok': True, 'demo': fallback_custom['demo_available']}
        result['status'], result['primary'], result['candidates'] = 'custom', cust, [cust]
        if customs:
            result['why'].append(f'Verified need "{need_txt}" points to a bespoke build ({", ".join(cust["hits"])}).')
        else:
            result['why'].append(f'Verified need "{need_txt}" does not match any approved standard product, '
                                 f'so a Custom Software Discovery is the safest next offer.')
    else:
        result['status'] = 'none'
        result['why'].append(f'Verified need "{need_txt}" does not match any approved offer and no custom-development '
                             f'offer is set up. A person should decide.')
    if result['status'] == 'matched' and any(c['kind'] == 'custom' for c in cands):
        result['why'].append('Custom development also mentioned; the standard product is recommended first.')
    result['match_key'] = f'{result["status"]}:' + ','.join(str(c["id"]) for c in result["candidates"])
    return result
