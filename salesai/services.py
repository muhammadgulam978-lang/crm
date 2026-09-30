"""Glue between the CRM tables and the rules engine, plus every action a salesperson can take."""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from . import rules
from .llm import AIUnavailable
from .models import (ACTION_TYPE_LABELS, ASSUMED, OFFER_ACTIONS, REJECTED, VERIFIED, CustomerFact, LeadInsight,
                     NextAction, OfferDecision, OverrideLog, QualificationQuestion, SalesAssistantSettings, SISService)
from .sources import fingerprint

logger = logging.getLogger('salesai')
_local = threading.local()
_executor = None


# ── configuration snapshots ──────────────────────────────────────────────────
def config_dict(cfg=None):
    cfg = cfg or SalesAssistantSettings.get()
    return {
        'target_countries': cfg.countries(), 'target_industries': cfg.industries(),
        'near_term_days': cfg.near_term_days, 'followup_days_researching': cfg.followup_days_researching,
        'question_wait_days': cfg.question_wait_days, 'ask_budget_after_engagement': cfg.ask_budget_after_engagement,
        'high_priority_score': cfg.high_priority_score, 'medium_priority_score': cfg.medium_priority_score,
        'min_known_factors': cfg.min_known_factors, 'weight_need': cfg.weight_need,
        'weight_readiness': cfg.weight_readiness, 'weight_authority': cfg.weight_authority,
        'weight_deal_size': cfg.weight_deal_size, 'weight_effort': cfg.weight_effort, 'weight_fit': cfg.weight_fit,
    }


def service_catalogue(include_drafts=False):
    """Entries the assistant may use. Draft / inactive entries are invisible to matching and to briefs."""
    qs = SISService.objects.filter(active=True)
    if not include_drafts:
        qs = qs.filter(status='approved')
    return [{'id': s.pk, 'name': s.name, 'label': s.offer_label, 'kind': s.kind, 'keywords': s.keyword_list(),
             'exclude_keywords': s.exclude_list(), 'industries': s.industry_list(), 'effort': s.effort,
             'demo_available': s.demo_available, 'description': s.description, 'features': s.feature_list(),
             'pricing_note': s.pricing_note, 'timeline_note': s.timeline_note, 'guarantees_note': s.guarantees_note,
             'cases': s.case_list(), 'objections': s.objection_list(), 'discovery_questions': s.discovery_question_list()}
            for s in qs]


def primary_opportunity(lead):
    from leads.models import Opportunity
    return (Opportunity.objects.filter(lead=lead).exclude(stage__in=['won', 'lost']).order_by('-created_at').first()
            or Opportunity.objects.filter(lead=lead).order_by('-created_at').first())


def stage_label(lead, opp=None):
    opp = opp or primary_opportunity(lead)
    if opp:
        return f'Opportunity: {opp.get_stage_display()}'
    return f'Lead: {lead.get_status_display()}'


def default_owner(lead):
    sp = lead.spo
    return sp.user if sp and sp.user_id else None


# ── state builder ────────────────────────────────────────────────────────────
def build_state(lead, cfg=None):
    from leads.models import CommunicationLog, EmailLog, Meeting, Opportunity, Proposal, Quotation
    cfg = cfg or config_dict()
    opp = primary_opportunity(lead)
    today = timezone.localdate()
    facts = [{'pk': f.pk, 'key': f.key, 'value': f.value, 'status': f.status, 'extra': f.extra or {},
              'resolved': f.resolved, 'confirmed': bool(f.confirmed_by_id),
              'source_label': f.source_label or ('Entered by staff' if f.origin != 'ai' else ''), 'evidence': f.evidence}
             for f in CustomerFact.objects.filter(lead=lead).exclude(status=REJECTED)]
    company_industry = ''
    company = getattr(opp, 'company', None) if opp else None
    if not company:
        contact = lead.contacts.select_related('company').first()
        company = contact.company if contact else None
    if company:
        company_industry = company.get_industry_display() if company.industry else ''
    inbound = (CommunicationLog.objects.filter(lead=lead, direction='inbound').exists()
               or EmailLog.objects.filter(lead=lead, direction='inbound').exists()
               or Meeting.objects.filter(lead=lead, status='completed').exists())
    try:
        from chatbot.models import ChatMessage, ChatSession
        from chatbot.services.utils import normalize_phone
        inbound = inbound or ChatMessage.objects.filter(
            direction='inbound',
            session__phone_number__in=[lead.contact_number, normalize_phone(lead.contact_number)]).exists()
    except Exception:
        pass
    upcoming = (Meeting.objects.filter(lead=lead, status__in=['scheduled', 're_scheduled'], scheduled_at__date__gte=today)
                .order_by('scheduled_at').first())
    if not upcoming and opp:
        upcoming = (Meeting.objects.filter(opportunity=opp, status__in=['scheduled', 're_scheduled'],
                                           scheduled_at__date__gte=today).order_by('scheduled_at').first())
    proposal_sent = (Proposal.objects.filter(lead=lead, status='sent').exists()
                     or Quotation.objects.filter(lead=lead, status='sent').exists()
                     or (opp and Quotation.objects.filter(opportunity=opp, status='sent').exists()))
    insight = LeadInsight.objects.filter(lead=lead).first()
    deal_value = (opp.value if opp and opp.value else (lead.quotation or None))
    history = list(Opportunity.objects.exclude(lead=lead).filter(value__gt=0).values_list('value', flat=True)[:500])
    questions = [{'field': q.field, 'status': q.status, 'text': q.text,
                  'asked_at': q.asked_at.date() if q.asked_at else None}
                 for q in QualificationQuestion.objects.filter(lead=lead)]
    return rules.LeadState(
        lead_id=lead.pk, lead_name=lead.name, country=lead.country or '', industry=company_industry,
        facts=facts, deal_value=Decimal(deal_value) if deal_value else None,
        deal_history=[Decimal(h) for h in history], has_two_way=bool(inbound),
        has_free_text=bool((lead.detail or '').strip()) or CommunicationLog.objects.filter(lead=lead).exists(),
        upcoming_meeting=upcoming.scheduled_at.date() if upcoming else None, proposal_sent=bool(proposal_sent),
        opp_stage=opp.stage if opp else '', lead_status=lead.status,
        ai_reviewed=bool(insight and insight.ai_reviewed_at), questions=questions,
        services=service_catalogue(), cfg=cfg, today=today)


def recompute_rules(lead, user=None):
    """Cheap, no AI: re-evaluates qualification / recommendation / priority from the current CRM data."""
    result = rules.assess(build_state(lead))
    insight, _ = LeadInsight.objects.get_or_create(lead=lead)
    insight.qualification, insight.recommendation, insight.priority = (
        result['qualification'], result['recommendation'], result['priority'])
    insight.offer_match = result['offer_match']
    insight.state = 'ready'
    insight.analyzed_at = timezone.now()
    insight.save()
    q = result['recommendation'].get('question')
    if q and not QualificationQuestion.objects.filter(lead=lead, field=q['field'], status__in=['suggested', 'asked']).exists():
        QualificationQuestion.objects.create(lead=lead, field=q['field'], text=q['text'])
    return insight


def is_stale(insight):
    return bool(insight.ai_reviewed_at and insight.ai_fingerprint and insight.ai_fingerprint != fingerprint(insight.lead))


def analyze_lead(lead, user=None, use_ai=True):
    """Full review: AI extracts facts (validated), then the rules decide. Falls back to rules-only if AI is down."""
    from .extraction import run_extraction
    cfg = SalesAssistantSettings.get()
    summary = {'ai': False, 'error': ''}
    insight, _ = LeadInsight.objects.get_or_create(lead=lead)
    if use_ai and cfg.ai_enabled:
        try:
            summary.update(run_extraction(lead, user))
            summary['ai'] = True
            insight.ai_reviewed_at = timezone.now()
            insight.ai_fingerprint = fingerprint(lead)
            insight.ai_error = ''
        except AIUnavailable as exc:
            insight.ai_error = str(exc)[:300]
            summary['error'] = str(exc)
        insight.save()
    insight = recompute_rules(lead, user)
    return summary, insight


# ── background review of new leads ───────────────────────────────────────────
def _executor_get():
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='salesai')
    return _executor


def suppress_auto_analysis():
    class _Ctx:
        def __enter__(s):
            _local.off = getattr(_local, 'off', 0) + 1
        def __exit__(s, *a):
            _local.off -= 1
    return _Ctx()


def auto_analysis_allowed():
    return not getattr(_local, 'off', 0)


def _job(lead_pk):
    from leads.models import Lead
    try:
        lead = Lead.objects.filter(pk=lead_pk).first()
        if lead:
            analyze_lead(lead, user=None, use_ai=True)
    except Exception:
        logger.exception('background lead review failed for lead %s', lead_pk)
    finally:
        connection.close()


def queue_background_review(lead_pk):
    """Runs the AI review off the request thread. Bounded queue so a bulk import cannot flood the AI service."""
    if not getattr(settings, 'SALESAI_BACKGROUND_ANALYSIS', True) or connection.vendor == 'sqlite':
        return False        # SQLite cannot be shared safely with a worker thread; use the "Analyze with AI" button
    ex = _executor_get()
    if ex._work_queue.qsize() >= 20:      # leave the rest 'pending' for `manage.py salesai_refresh --pending`
        return False
    ex.submit(_job, lead_pk)
    return True


# ── facts ────────────────────────────────────────────────────────────────────
def add_manual_fact(lead, user, key, value, bucket='', role='', note=''):
    extra = {}
    if key == 'timeline' and bucket:
        extra['bucket'] = bucket
    if key == 'decision_role' and role:
        extra['role'] = role
    f = CustomerFact.objects.create(
        lead=lead, key=key, value=value.strip()[:400], status=VERIFIED, extra=extra, origin='manual',
        speaker='staff', source_label=f'Entered by {user.get_username()}', note=note[:300],
        confirmed_by=user, confirmed_at=timezone.now(), created_by=user)
    recompute_rules(lead, user)
    return f


def set_fact_status(fact, user, action):
    if action == 'confirm':
        fact.status, fact.confirmed_by, fact.confirmed_at = VERIFIED, user, timezone.now()
        fact.note = ''
    elif action == 'reject':
        fact.status = REJECTED
    elif action == 'resolve':
        fact.resolved = True
    elif action == 'unresolve':
        fact.resolved = False
    else:
        return False
    fact.save()
    recompute_rules(fact.lead, user)
    return True


# ── next action (with override tracking) ─────────────────────────────────────
def _due_from(rec, today=None):
    today = today or timezone.localdate()
    if rec.get('due_date'):
        try:
            return date.fromisoformat(rec['due_date'])
        except ValueError:
            pass
    return today + timedelta(days=int(rec.get('due_in_days', 2) or 0))


def recommended_snapshot(lead, rec):
    owner = default_owner(lead)
    return {'code': rec.get('code'), 'action_type': rec.get('action_type'), 'action': rec.get('action'),
            'due_date': _due_from(rec).isoformat(), 'owner_id': owner.pk if owner else None,
            'reason': rec.get('reason'), 'product': (rec.get('product') or {}).get('label', '')}


@transaction.atomic
def set_next_action(lead, user, *, action_type, action, owner, due_date, reason='', origin=None, ai_rec=None,
                    opportunity=None):
    """
    Creates the single OPEN next action for the lead / opportunity. If it differs from the AI recommendation
    (type, wording, owner or due date) it is stored as an override and written to the OverrideLog.
    """
    opportunity = opportunity or primary_opportunity(lead)
    if ai_rec is None:
        insight = LeadInsight.objects.filter(lead=lead).first()
        ai_rec = recommended_snapshot(lead, insight.recommendation) if insight and insight.recommendation else {}
    chosen = {'action_type': action_type, 'action': action.strip(),
              'due_date': due_date.isoformat() if due_date else None, 'owner_id': owner.pk if owner else None}
    changed = []
    if ai_rec:
        for f in ('action_type', 'action', 'due_date', 'owner_id'):
            if (ai_rec.get(f) or None) != (chosen.get(f) or None):
                changed.append(f)
    is_override = bool(ai_rec) and bool(changed)
    origin = origin or ('human' if not ai_rec else 'ai_edited' if is_override else 'ai_accepted')

    NextAction.objects.filter(lead=lead, status='open').update(status='superseded')
    na = NextAction.objects.create(
        lead=lead, opportunity=opportunity, stage_label=stage_label(lead, opportunity), action_type=action_type,
        action=action.strip()[:400], owner=owner, due_date=due_date, origin=origin, ai_recommendation=ai_rec or {},
        is_override=is_override, override_reason=reason[:400] if is_override else '', created_by=user)
    if is_override:
        OverrideLog.objects.create(kind='next_action', lead=lead, opportunity=opportunity, user=user,
                                   ai_value=ai_rec, chosen_value=chosen, changed_fields=changed, reason=reason[:400])
    _mirror_activity(user, lead, f'Next action set: {na.action} (due {due_date or "not set"})'
                     + (' - changed from the AI recommendation' if is_override else ''))
    # a "research" recommendation carries a follow-up date: create it in the existing Follow-up list
    return na


def accept_recommendation(lead, user, owner=None, due_date=None):
    insight = LeadInsight.objects.get(lead=lead)
    rec = insight.recommendation
    snap = recommended_snapshot(lead, rec)
    owner = owner or default_owner(lead) or user
    na = set_next_action(lead, user, action_type=rec['action_type'], action=rec['action'], owner=owner,
                         due_date=due_date or date.fromisoformat(snap['due_date']), ai_rec=snap)
    if rec.get('followup_in_days') and rec.get('code') == 'INFO_FOLLOWUP':
        from leads.models import FollowUp
        FollowUp.objects.create(
            lead=lead, follow_up_date=timezone.now() + timedelta(days=int(rec['followup_in_days'])),
            notes='Follow up after sending information (created from accepted AI recommendation).')
    return na


def complete_next_action(na, user):
    na.status, na.completed_at = 'done', timezone.now()
    na.save()
    if na.lead_id:
        _mirror_activity(user, na.lead, f'Next action completed: {na.action}')
        recompute_rules(na.lead, user)


def dismiss_recommendation(lead, user, reason=''):
    insight = LeadInsight.objects.get(lead=lead)
    OverrideLog.objects.create(kind='recommendation_dismissed', lead=lead, opportunity=primary_opportunity(lead),
                               user=user, ai_value=recommended_snapshot(lead, insight.recommendation),
                               changed_fields=['dismissed'], reason=reason[:400])
    insight.recommendation_dismissed_at = timezone.now()
    insight.save(update_fields=['recommendation_dismissed_at'])


# ── priority (AI recommends, salesperson decides) ────────────────────────────
def decide_priority(lead, user, level, reason=''):
    insight = LeadInsight.objects.get(lead=lead)
    ai_level = (insight.priority or {}).get('level', '')
    insight.priority_final, insight.priority_final_by, insight.priority_final_at = level, user, timezone.now()
    insight.save()
    if ai_level in ('high', 'medium', 'low') and ai_level != level:
        OverrideLog.objects.create(kind='priority', lead=lead, opportunity=primary_opportunity(lead), user=user,
                                   ai_value={'level': ai_level, 'score': insight.priority.get('score')},
                                   chosen_value={'level': level}, changed_fields=['priority'], reason=reason[:400])
    opp = primary_opportunity(lead)
    if opp and opp.stage not in ('won', 'lost') and opp.priority != level:
        opp.priority = level
        opp.save(update_fields=['priority'])
    _mirror_activity(user, lead, f'Sales priority decided: {level}')


# ── question tracking ────────────────────────────────────────────────────────
def mark_question(q, user, action):
    if action == 'asked':
        q.status, q.asked_by, q.asked_at = 'asked', user, timezone.now()
    elif action in ('answered', 'dismissed'):
        q.status = action
    else:
        return False
    q.save()
    recompute_rules(q.lead, user)
    return True


# ── close out-of-market lead (explicit salesperson action) ───────────────────
@transaction.atomic
def close_out_of_market(lead, user, reason):
    from leads.models import CommunicationLog, Opportunity
    reason = reason.strip()
    CommunicationLog.objects.create(
        comm_type='note', direction='internal', subject='Lead closed - outside target market', body=reason,
        created_by=user, lead=lead)
    lead.status = 'lost'
    lead.save()
    for opp in Opportunity.objects.filter(lead=lead).exclude(stage__in=['won', 'lost']):
        opp.stage, opp.probability, opp.lost_reason = 'lost', 0, reason
        opp.save()
    NextAction.objects.filter(lead=lead, status='open').update(status='cancelled')
    _mirror_activity(user, lead, f'Lead closed as outside target market: {reason}')
    recompute_rules(lead, user)


def _mirror_activity(user, lead, text):
    try:
        from leads.models import log_activity
        log_activity(user if getattr(user, 'is_authenticated', False) else None, 'updated', 'Lead', lead.pk,
                     lead.name, f'[AI Sales Assistant] {text}'[:500])
    except Exception:
        logger.debug('activity mirror failed', exc_info=True)


# ── offer decision: the Sales Lead owns the commercial decision ─────────────────────────────────────────────
NO_OFFER = 'none'
from .models import ACTION_TYPES as ACTION_TYPES_MAP


def offer_match_key(insight):
    m, r = insight.offer_match or {}, insight.recommendation or {}
    return f'{m.get("match_key", m.get("status", ""))}|{r.get("action_type", "")}'


@transaction.atomic
def decide_offer(lead, user, *, accept, product_id='', action_type='', reason='', owner=None, due_date=None):
    """
    Accept: the AI recommendation becomes the next action.
    Override: product and/or action are chosen by the Sales Lead; a reason is mandatory.
    Both are recorded (OfferDecision); overrides are also written to the OverrideLog.
    Returns (decision, error_message).
    """
    insight = LeadInsight.objects.filter(lead=lead).first()
    if not insight or not insight.recommendation:
        return None, 'There is no AI recommendation yet. Run the analysis first.'
    rec, match = insight.recommendation, insight.offer_match or {}
    ai_ref = rec.get('product') or {}
    ai_product = SISService.objects.filter(pk=ai_ref.get('id')).first() if ai_ref.get('id') else None
    ai_label = ai_ref.get('label', '')
    opp = primary_opportunity(lead)
    snap = recommended_snapshot(lead, rec)
    common = dict(lead=lead, opportunity=opp, ai_product=ai_product, ai_product_label=ai_label,
                  ai_action_type=rec.get('action_type', ''), ai_match={'match': match, 'recommendation': rec},
                  match_key=offer_match_key(insight), decided_by=user)
    owner = owner or default_owner(lead) or user

    if accept:
        services_accept = accept_recommendation(lead, user, owner=owner, due_date=due_date)
        d = OfferDecision.objects.create(decision='accepted', chosen_product=ai_product, chosen_product_label=ai_label,
                                         chosen_action_type=rec.get('action_type', ''), **common)
        _mirror_activity(user, lead, f'Accepted AI recommendation: {ai_label or "no product"} / {rec.get("action")}')
        return d, ''

    reason = (reason or '').strip()
    if len(reason) < 10:
        return None, 'Please record why you are overriding the AI recommendation (at least a short sentence).'
    if action_type not in OFFER_ACTIONS + ['ask_question', 'close_out_of_market', 'follow_up', 'send_proposal', 'prepare_meeting', 'other']:
        return None, 'Choose the next action.'
    if product_id == NO_OFFER:
        chosen, chosen_label = None, 'No SIS offer (not a fit)'
    elif str(product_id).isdigit():
        chosen = SISService.objects.filter(pk=product_id, status='approved', active=True).first()
        if not chosen:
            return None, 'Only approved products can be chosen.'
        chosen_label = chosen.offer_label
    else:
        chosen, chosen_label = ai_product, ai_label
    label = dict(ACTION_TYPES_MAP).get(action_type, action_type)
    action_text = f'{label}: {chosen_label}' if chosen_label and action_type in ('demo', 'discovery_meeting', 'followup_info') else label
    set_next_action(lead, user, action_type=action_type, action=action_text, owner=owner, due_date=due_date or timezone.localdate() + timedelta(days=2),
                    reason=reason, ai_rec=snap, opportunity=opp)
    changed = [f for f, a, b in (('product', ai_label, chosen_label), ('action_type', rec.get('action_type'), action_type)) if a != b]
    d = OfferDecision.objects.create(decision='overridden', chosen_product=chosen, chosen_product_label=chosen_label,
                                     chosen_action_type=action_type, reason=reason[:500], **common)
    OverrideLog.objects.create(kind='offer', lead=lead, opportunity=opp, user=user,
                               ai_value={'product': ai_label, 'action': rec.get('action'), 'action_type': rec.get('action_type')},
                               chosen_value={'product': chosen_label, 'action': action_text, 'action_type': action_type},
                               changed_fields=changed or ['confirmed_with_changes'], reason=reason[:400])
    _mirror_activity(user, lead, f'Overrode AI offer recommendation ({ai_label or "none"} -> {chosen_label}): {reason}')
    return d, ''


def latest_decision(lead):
    return OfferDecision.objects.filter(lead=lead).select_related('decided_by').first()
