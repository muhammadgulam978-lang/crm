"""
The decision layer. Pure Python - no database, no AI.

The language model is only allowed to EXTRACT facts (with quotes) from CRM records.
Everything that decides what happens next - what is missing, which question to ask,
which action to recommend, which priority - is computed here from VERIFIED facts only,
so the AI can never invent its way into a recommendation.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from . import offers
from .models import BUCKET_DAYS, FACT_KEY_LABELS, normalize_text

QUESTION_TEXT = {
    'need': ('Could you tell us a little about what you are trying to achieve, '
             'or which problem you would like the software to solve?'),
    'timeline': 'When are you hoping to have a solution in place, or to make a decision?',
    'decision_role': 'Will you be the one deciding on this, or is anyone else involved in the decision?',
    'budget': 'Do you have an approximate budget range in mind, so we can propose the right scope?',
    'industry': 'Which industry or type of organisation is this for?',
}
FIELD_LABELS = {'need': 'Customer need', 'timeline': 'Buying timeline',
                'decision_role': 'Decision-making role', 'budget': 'Budget range', 'industry': 'Industry'}


@dataclass
class LeadState:
    lead_id: int = 0
    lead_name: str = ''
    country: str = ''
    industry: str = ''
    facts: list = field(default_factory=list)        # dicts: pk,key,value,status,extra,resolved,source_label,evidence
    deal_value: Optional[Decimal] = None
    deal_history: list = field(default_factory=list)
    has_two_way: bool = False                        # customer has replied / a meeting happened
    has_free_text: bool = False                      # enquiry text / notes exist that the AI has not read yet
    upcoming_meeting: Optional[date] = None
    proposal_sent: bool = False
    opp_stage: str = ''
    lead_status: str = 'new'
    ai_reviewed: bool = False
    questions: list = field(default_factory=list)    # dicts: field,status,asked_at(date|None),text
    services: list = field(default_factory=list)     # dicts: id,name,keywords,effort,cases,objections
    cfg: dict = field(default_factory=dict)
    today: date = field(default_factory=date.today)


# ── helpers ──────────────────────────────────────────────────────────────────
def verified(state, key):
    """Newest verified, unrejected fact for `key` (salesperson-confirmed facts win)."""
    hits = [f for f in state.facts if f['key'] == key and f['status'] == 'verified' and not f.get('resolved')]
    hits.sort(key=lambda f: (bool(f.get('confirmed')), f.get('pk', 0)), reverse=True)
    return hits[0] if hits else None


def all_verified(state, key):
    return [f for f in state.facts if f['key'] == key and f['status'] == 'verified']


def assumed(state, key):
    return [f for f in state.facts if f['key'] == key and f['status'] == 'assumed']


def _short(text, n=90):
    text = (text or '').strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + '…'


def _basis(f):
    return f'"{_short(f["value"])}" ({f.get("source_label") or "CRM record"})'


def match_services(state):
    """Approved catalogue entries the offer matcher selected (used by priority, briefs and the panel)."""
    m = offers.match_offers(state)
    ids = {c['id'] for c in m['candidates']}
    return [s for s in state.services if s['id'] in ids]


# ── market fit ───────────────────────────────────────────────────────────────
def market_fit(state):
    tc = state.cfg.get('target_countries') or []
    ti = state.cfg.get('target_industries') or []
    country = normalize_text(state.country)
    industry = normalize_text(state.industry)
    reasons, outside, inside, judged = [], False, True, False
    if tc and country:
        judged = True
        if country in tc:
            reasons.append(f'Country "{state.country}" is in the target list.')
        else:
            outside = True
            reasons.append(f'Country "{state.country}" is not in the SIS target countries.')
    elif tc:
        inside = False
        reasons.append('Country not recorded, so market fit cannot be judged.')
    if ti and industry and industry != 'other':
        judged = True
        if industry in ti:
            reasons.append(f'Industry "{state.industry}" is in the target list.')
        else:
            outside = True
            reasons.append(f'Industry "{state.industry}" is not in the SIS target industries.')
    elif ti:
        inside = False
        reasons.append('Industry not recorded, so it was not judged.')
    if outside:
        status = 'outside'
    elif judged and inside:
        status = 'inside'
    else:
        status = 'unknown'
    return {'status': status, 'reasons': reasons}


# ── qualification ────────────────────────────────────────────────────────────
def near_term(state, bucket):
    return bool(bucket) and BUCKET_DAYS.get(bucket, 999) <= state.cfg.get('near_term_days', 90)


def qualification(state):
    need = verified(state, 'need')
    tl = verified(state, 'timeline')
    role = verified(state, 'decision_role')
    budget = verified(state, 'budget')
    tl_bucket = (tl or {}).get('extra', {}).get('bucket', '') if tl else ''
    ind_value, ind_src, ind_status = offers.known_industry(state)
    fields = {}
    for key, f in (('need', need), ('timeline', tl if tl_bucket else None), ('decision_role', role), ('budget', budget)):
        a = assumed(state, key)
        fields[key] = {
            'label': FIELD_LABELS[key],
            'status': 'verified' if f else ('assumed' if a else 'unknown'),
            'value': f['value'] if f else (a[0]['value'] if a else ''),
            'source': (f or (a[0] if a else {})).get('source_label', '') if (f or a) else '',
        }
    ind_assumed = assumed(state, 'industry')
    fields['industry'] = {'label': FIELD_LABELS['industry'],
                          'status': 'verified' if ind_value else ('assumed' if ind_assumed else 'unknown'),
                          'value': ind_value or (ind_assumed[0]['value'] if ind_assumed else ''), 'source': ind_src}
    # budget and industry are only "missing" when they matter for the next sales decision
    missing = [k for k in ('need', 'timeline', 'decision_role', 'budget') if fields[k]['status'] != 'verified']
    fit = market_fit(state)
    qualified = bool(need and tl_bucket) and fit['status'] != 'outside'
    return {'fields': fields, 'missing': missing, 'market_fit': fit, 'qualified': qualified,
            'timeline_bucket': tl_bucket, 'ai_reviewed': state.ai_reviewed}


def _budget_appropriate(state, q):
    if q['fields']['budget']['status'] == 'verified':
        return False
    if not state.cfg.get('ask_budget_after_engagement', True):
        return True
    return q['fields']['need']['status'] == 'verified' and bool(q['timeline_bucket']) and state.has_two_way


def pick_question(state, q, recommend_meeting, match=None):
    """The ONE question worth asking now, or None. Skips anything known, dismissed or already asked."""
    if q['market_fit']['status'] == 'outside':
        return None
    order = ['need', 'timeline']
    if match and match.get('needs_industry') and q['fields']['industry']['status'] != 'verified':
        order.insert(1, 'industry')          # only when the answer decides which product fits
    if not recommend_meeting:                # a meeting will cover authority / budget - do not pre-interrogate
        order.append('decision_role')
        if _budget_appropriate(state, q):
            order.append('budget')
    for key in order:
        if q['fields'][key]['status'] == 'verified':
            continue
        prior = [x for x in state.questions if x['field'] == key]
        if any(x['status'] == 'dismissed' for x in prior):
            continue
        text = QUESTION_TEXT[key]
        if key == 'timeline' and q['fields']['need']['status'] == 'verified':
            text = f'For {_short(q["fields"]["need"]["value"], 70)}: ' + QUESTION_TEXT['timeline']
        asked = next((x for x in prior if x['status'] == 'asked'), None)
        return {'field': key, 'label': FIELD_LABELS[key], 'text': text, 'already_asked': bool(asked),
                'asked_at': asked['asked_at'].isoformat() if asked and asked.get('asked_at') else ''}
    return None


# ── recommendation ───────────────────────────────────────────────────────────
def _offer_text(match):
    if match['status'] == 'ambiguous':
        return ' or '.join(c['label'] for c in match['candidates'])
    return match['primary']['label'] if match.get('primary') else ''


def recommend(state, q, match=None):
    """
    Next action. Five commercial outcomes: demo | discovery_meeting | followup_info | nurture | human_review
    (plus supporting ones: ask one question, prepare a booked meeting, follow up a proposal, close out of market).
    """
    match = match or offers.match_offers(state)
    cfg, today = state.cfg, state.today
    need, tl = verified(state, 'need'), verified(state, 'timeline')
    fit = q['market_fit']
    early = state.opp_stage in ('', 'new', 'contacted') and state.lead_status in ('new', 'positive')
    svc = [s for s in state.services if s['id'] in {c['id'] for c in match['candidates']}]
    base = {'service_names': [s['name'] for s in svc], 'guardrails': [], 'product': _product_ref(match)}

    if fit['status'] == 'outside' and early:
        return {**base, 'code': 'OUT_OF_MARKET', 'action_type': 'close_out_of_market',
                'action': 'Record the reason and close the lead politely',
                'reason': ' '.join(r for r in fit['reasons'] if 'not in' in r),
                'due_in_days': 2, 'question': None,
                'suggested_reason': 'Outside target market: ' + ' '.join(r for r in fit['reasons'] if 'not in' in r)}

    if state.upcoming_meeting:
        return {**base, 'code': 'MEETING_SCHEDULED', 'action_type': 'prepare_meeting',
                'action': f'Review the call brief and prepare for the meeting on {state.upcoming_meeting:%d %b %Y}',
                'reason': 'A meeting is already scheduled for this lead.',
                'due_date': state.upcoming_meeting.isoformat(), 'question': None}

    if state.proposal_sent and state.opp_stage in ('proposal', 'negotiation'):
        return {**base, 'code': 'PROPOSAL_FOLLOWUP', 'action_type': 'send_proposal',
                'action': 'Follow up on the proposal that has been sent',
                'reason': f'Opportunity is in "{state.opp_stage}" and a proposal has already gone out.',
                'due_in_days': 3, 'question': None}

    # the AI has never read the free text: do not ask the customer things the enquiry may already answer
    if not state.ai_reviewed and state.has_free_text and not need:
        return {**base, 'code': 'REVIEW_FIRST', 'action_type': 'review_first',
                'action': 'Read the enquiry text and run "Analyze with AI" before asking the customer anything',
                'reason': 'There is enquiry text or notes that has not been reviewed yet, so the need may already be known.',
                'due_in_days': 0, 'question': None}

    tl_bucket = q['timeline_bucket']
    if need and tl_bucket:
        soon = near_term(state, tl_bucket)
        offer = _offer_text(match)
        prim = match.get('primary')
        why = ' '.join(match['why'][:1])
        tl_txt = f'Timeline {_basis(tl)}.'

        # an unresolved product choice is cheaper to settle by asking than by guessing
        q_first = None
        if match['needs_industry'] and q['fields']['industry']['status'] != 'verified':
            q_first = pick_question(state, q, recommend_meeting=True, match=match)
            if q_first and q_first['field'] != 'industry':
                q_first = None
        if q_first and not q_first['already_asked']:
            return {**base, 'code': 'ASK_ONE_QUESTION', 'action_type': 'ask_question',
                    'action': 'Ask one question: which industry / type of organisation this is for',
                    'reason': 'Approved offers are industry-specific and the customer\'s industry is not verified yet, '
                              'so the right product cannot be chosen.', 'due_in_days': 1, 'question': q_first}

        if match['status'] == 'none' or match['status'] == 'no_need':
            return {**base, 'code': 'HUMAN_REVIEW', 'action_type': 'human_review',
                    'action': 'Human review: decide which SIS offer fits this need',
                    'reason': ' '.join(match['why']) or 'No approved offer matched the verified need.',
                    'due_in_days': 2, 'question': None}

        if match['status'] == 'custom':
            what, code = 'Custom Software Discovery', 'CUSTOM_DISCOVERY'
        elif match['status'] == 'ambiguous':
            what, code = f'discovery to choose between {offer}', 'AMBIGUOUS_DISCOVERY'
        else:
            what, code = offer, 'MATCHED'

        if tl_bucket == 'long':
            return {**base, 'code': 'NURTURE', 'action_type': 'nurture',
                    'action': f'Nurture: stay in touch until the decision date ({what})',
                    'reason': f'{why} {tl_txt} The decision is further away than 6 months.'.strip(),
                    'due_in_days': cfg.get('followup_days_researching', 7) * 4, 'question': None}
        if soon:
            if match['status'] == 'matched' and prim and prim['demo']:
                return {**base, 'code': 'DEMO', 'action_type': 'demo', 'action': f'Book a demo of {offer}',
                        'reason': f'{why} {tl_txt} A standard demo exists for this offer.'.strip(),
                        'due_in_days': 2, 'question': None}
            extra = ('' if match['status'] != 'matched' else ' No standard demo is set up for this offer, so start with a discovery meeting.')
            return {**base, 'code': code, 'action_type': 'discovery_meeting',
                    'action': f'Book a discovery meeting: {what}', 'reason': f'{why} {tl_txt}{extra}'.strip(),
                    'due_in_days': 2, 'question': None}
        days = cfg.get('followup_days_researching', 7)
        cases = [c for s in svc for c in s['cases']]
        if not cases:
            base['guardrails'].append('No approved case example / material exists for this offer. Send only approved information, '
                                      'or add material in the Product Knowledge Base.')
        return {**base, 'code': 'INFO_FOLLOWUP', 'action_type': 'followup_info',
                'action': (f'Send approved information about {offer}' if match['status'] == 'matched'
                           else f'Send approved information and follow up ({what})'),
                'reason': f'{why} {tl_txt} The decision is not near-term.'.strip(),
                'due_in_days': 2, 'followup_in_days': days, 'question': None}

    question = pick_question(state, q, recommend_meeting=False, match=match)
    if question and question['already_asked']:
        asked = date.fromisoformat(question['asked_at'][:10]) if question['asked_at'] else today
        wait = cfg.get('question_wait_days', 3)
        due = asked + timedelta(days=wait)
        return {**base, 'code': 'AWAIT_ANSWER', 'action_type': 'follow_up',
                'action': f'Follow up on the question already asked ({question["label"].lower()})',
                'reason': f'The {question["label"].lower()} question was asked on {asked:%d %b}; no answer is recorded yet.',
                'due_date': max(due, today).isoformat(), 'question': question}
    if question:
        return {**base, 'code': 'ASK_ONE_QUESTION', 'action_type': 'ask_question',
                'action': f'Ask one question: {question["label"].lower()}',
                'reason': f'{question["label"]} is not yet verified and blocks the next step.',
                'due_in_days': 1, 'question': question}
    return {**base, 'code': 'HUMAN_REVIEW', 'action_type': 'human_review',
            'action': 'Human review: decide the next step for this lead',
            'reason': 'Nothing blocking was found, but there is not enough verified information to recommend a specific step.',
            'due_in_days': 2, 'question': None}


def _product_ref(match):
    if match.get('primary'):
        p = match['primary']
        return {'id': p['id'], 'label': p['label'], 'status': match['status']}
    if match['status'] == 'ambiguous':
        return {'id': None, 'label': ' or '.join(c['label'] for c in match['candidates']), 'status': 'ambiguous',
                'ids': [c['id'] for c in match['candidates']]}
    return {'id': None, 'label': '', 'status': match['status']}


# ── sales priority ───────────────────────────────────────────────────────────
def priority(state, q):
    cfg = state.cfg
    factors = []

    def add(key, label, weight, points, why, known=True):
        factors.append({'key': key, 'label': label, 'weight': weight, 'points': points if known else None,
                        'known': known, 'why': why})

    # 1 strength of need
    need = verified(state, 'need')
    if need:
        pts = 3 + (1 if len(all_verified(state, 'requirement')) >= 2 else 0) + (1 if all_verified(state, 'pain_point') else 0)
        add('need', 'Strength of need', cfg['weight_need'], pts,
            f'Verified need {_basis(need)}; {len(all_verified(state, "requirement"))} verified requirement(s).')
    else:
        add('need', 'Strength of need', cfg['weight_need'], 0, 'No verified need yet.', known=False)
    # 2 buying readiness
    b = q['timeline_bucket']
    if b:
        pts = {'immediate': 5, 'near': 4, 'medium': 2, 'long': 1, 'researching': 1}[b]
        add('readiness', 'Buying readiness', cfg['weight_readiness'], pts, f'Verified timeline: {b}.')
    else:
        add('readiness', 'Buying readiness', cfg['weight_readiness'], 0, 'Timeline not verified.', known=False)
    # 3 decision-maker access
    role = verified(state, 'decision_role')
    r = (role or {}).get('extra', {}).get('role') if role else ''
    if r:
        pts = {'decision_maker': 5, 'influencer': 3, 'user': 2, 'gatekeeper': 1}.get(r, 0)
        add('authority', 'Decision-maker access', cfg['weight_authority'], pts, f'Verified role: {r.replace("_", " ")}.')
    else:
        add('authority', 'Decision-maker access', cfg['weight_authority'], 0, 'Decision-making role not verified.', known=False)
    # 4 expected deal size (relative to SIS's own deal history - no invented currency thresholds)
    hist = [float(x) for x in state.deal_history if x and x > 0]
    if state.deal_value and state.deal_value > 0 and len(hist) >= 5:
        share = sum(1 for h in hist if h <= float(state.deal_value)) / len(hist)
        pts = 5 if share >= .75 else 4 if share >= .5 else 3 if share >= .25 else 2
        add('deal_size', 'Expected deal size', cfg['weight_deal_size'], pts,
            f'Recorded value is larger than {round(share * 100)}% of SIS deals on record.')
    elif state.deal_value and state.deal_value > 0:
        add('deal_size', 'Expected deal size', cfg['weight_deal_size'], 0,
            'A value is recorded, but fewer than 5 deals exist to compare it with.', known=False)
    else:
        add('deal_size', 'Expected deal size', cfg['weight_deal_size'], 0, 'No deal value recorded.', known=False)
    # 5 delivery effort  /  6 fit  (both come from the SIS service catalogue)
    svc = match_services(state)
    efforts = [s['effort'] for s in svc if s['effort'] in ('low', 'medium', 'high')]
    if efforts:
        worst = 'high' if 'high' in efforts else 'medium' if 'medium' in efforts else 'low'
        add('effort', 'Delivery effort', cfg['weight_effort'], {'low': 5, 'medium': 3, 'high': 1}[worst],
            f'Matched service effort: {worst}.')
    else:
        add('effort', 'Delivery effort', cfg['weight_effort'], 0, 'No matched service with an effort level set.', known=False)
    if not state.services:
        add('fit', 'Fit with SIS services', cfg['weight_fit'], 0, 'No SIS services are configured yet (Settings > Services).', known=False)
    elif not need:
        add('fit', 'Fit with SIS services', cfg['weight_fit'], 0, 'Needs a verified need to compare with services.', known=False)
    elif svc:
        add('fit', 'Fit with SIS services', cfg['weight_fit'], 5, f'Matches: {", ".join(s["name"] for s in svc)}.')
    else:
        add('fit', 'Fit with SIS services', cfg['weight_fit'], 0, 'Need does not match any configured SIS service.')

    known = [f for f in factors if f['known']]
    total_w = sum(f['weight'] for f in known)
    score = round(sum(f['weight'] * f['points'] / 5 for f in known) / total_w * 100) if total_w else 0
    complete = len(known) >= cfg.get('min_known_factors', 3)
    if q['market_fit']['status'] == 'outside':
        level = 'not_pursued'
    elif not complete:
        level = 'insufficient'
    else:
        level = 'high' if score >= cfg['high_priority_score'] else 'medium' if score >= cfg['medium_priority_score'] else 'low'
    return {'score': score if complete else None, 'level': level, 'factors': factors,
            'known_count': len(known), 'total_factors': len(factors),
            'missing_factors': [f['label'] for f in factors if not f['known']]}


def assess(state):
    match = offers.match_offers(state)
    q = qualification(state)
    rec = recommend(state, q, match)
    pr = priority(state, q)
    return {'qualification': q, 'recommendation': rec, 'priority': pr, 'offer_match': match}
