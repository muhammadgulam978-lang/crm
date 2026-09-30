"""
AI Sales Assistant views. Read pages need the existing "leads" access; every state-changing action is a POST
protected by the CRM's own block_view_only decorator, so view-only users cannot change anything.
"""
from collections import Counter
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_POST

from leads.decorators import block_view_only, role_required
from leads.models import Lead, Meeting

from . import portfolio, postcall, rules, services
from .brief import generate_brief
from .models import (ACTION_TYPES, EFFORT_CHOICES, FACT_KEYS, FACT_KEY_LABELS, OFFER_ACTIONS, ROLE_CHOICES,
                     TIMELINE_BUCKETS, CallBrief, ConversationDraft, CustomerFact, LeadInsight, NextAction,
                     OfferDecision, OverrideLog, QualificationQuestion, SalesAssistantSettings, SISService)

User = get_user_model()
manager_only = role_required('admin', 'sales_manager', 'ceo')


def _lead(pk):
    return get_object_or_404(Lead, pk=pk)


def _back(pk):
    return redirect('salesai:workspace', pk=pk)


def _users():
    return User.objects.filter(is_active=True).order_by('username')


def _user_or_none(value):
    return _users().filter(pk=value).first() if str(value or '').isdigit() else None


# ── workspace (one lead, everything) ─────────────────────────────────────────
@login_required
def workspace(request, pk):
    lead = _lead(pk)
    insight = services.recompute_rules(lead, request.user)        # cheap, no AI; keeps the page current
    facts = list(CustomerFact.objects.filter(lead=lead).exclude(status='rejected').select_related('confirmed_by'))
    by_key = {k: [f for f in facts if f.key == k] for k, _ in FACT_KEYS}
    opp = services.primary_opportunity(lead)
    na = NextAction.objects.filter(lead=lead, status='open').select_related('owner').first()
    rec = insight.recommendation or {}
    snap = services.recommended_snapshot(lead, rec) if rec else {}
    brief = CallBrief.objects.filter(lead=lead).first()
    qualified = (insight.qualification or {}).get('qualified')
    structured = [('Country', lead.country), ('City', lead.city), ('Lead source', lead.get_lead_source_display()),
                  ('Recorded quotation / value', opp.value if opp and opp.value else (lead.quotation or '')),
                  ('Opportunity stage', opp.get_stage_display() if opp else '')]
    return render(request, 'salesai/workspace.html', {
        'active': 'salesai', 'lead': lead, 'insight': insight, 'opp': opp, 'next_action': na,
        'q': insight.qualification or {}, 'rec': rec, 'snap': snap, 'prio': insight.priority or {},
        'facts_by_key': [(k, FACT_KEY_LABELS[k], by_key[k]) for k, _ in FACT_KEYS if by_key[k]],
        'structured': [(a, b) for a, b in structured if b],
        'stale': services.is_stale(insight), 'brief': brief, 'qualified': qualified,
        'brief_unreviewed': bool(brief and not brief.reviewed_at),
        'questions': QualificationQuestion.objects.filter(lead=lead, status__in=['suggested', 'asked']),
        'drafts': ConversationDraft.objects.filter(lead=lead)[:5],
        'overrides': OverrideLog.objects.filter(lead=lead).select_related('user')[:6],
        'history': NextAction.objects.filter(lead=lead).exclude(status='open').select_related('owner')[:6],
        'users': _users(), 'action_types': ACTION_TYPES, 'fact_keys': [k for k in FACT_KEYS if k[0] != 'other'],
        'buckets': TIMELINE_BUCKETS, 'roles': ROLE_CHOICES, 'today': timezone.localdate(),
        'default_owner': services.default_owner(lead) or request.user,
        'meetings': Meeting.objects.filter(lead=lead).order_by('-scheduled_at')[:8],
        'title': f'AI Sales Assistant - {lead.name}',
        **offer_context(lead, insight),
    })


def offer_context(lead, insight):
    """Everything the offer card needs (shared by the workspace and the Lead / Opportunity panel)."""
    match = insight.offer_match or {}
    dec = services.latest_decision(lead)
    return {
        'match': match, 'decision': dec,
        'decision_stale': bool(dec and dec.match_key != services.offer_match_key(insight)),
        'approved_offers': SISService.objects.filter(status='approved', active=True),
        'offer_actions': [(a, dict(ACTION_TYPES)[a]) for a in OFFER_ACTIONS],
    }


@login_required
@block_view_only
@require_POST
def offer_decide(request, pk):
    lead = _lead(pk)
    p = request.POST
    accept = p.get('choice') == 'accept'
    d, err = services.decide_offer(
        lead, request.user, accept=accept, product_id=p.get('product', ''), action_type=p.get('action_type', ''),
        reason=p.get('reason', ''), owner=_user_or_none(p.get('owner')), due_date=parse_date(p.get('due_date', '') or ''))
    if err:
        messages.error(request, err)
    else:
        messages.success(request, 'Recommendation accepted. Next action saved.' if accept else
                         'Your decision was saved with your reason. The AI recommendation is kept for comparison.')
    nxt = p.get('next') or ''
    return redirect(nxt) if nxt.startswith('/') and not nxt.startswith('//') else _back(pk)


@login_required
@block_view_only
@require_POST
def analyze(request, pk):
    lead = _lead(pk)
    summary, _ = services.analyze_lead(lead, request.user, use_ai=True)
    if summary.get('ai'):
        messages.success(request, f'AI review finished: {summary.get("verified", 0)} verified, '
                                  f'{summary.get("assumed", 0)} assumption(s) from {summary.get("sources", 0)} CRM record(s). '
                                  f'Please confirm or reject the assumptions.')
    else:
        messages.error(request, (summary.get('error') or 'AI review did not run.') +
                                ' The rules were re-checked using the information already in the CRM.')
    return _back(pk)


# ── facts ────────────────────────────────────────────────────────────────────
@login_required
@block_view_only
@require_POST
def fact_add(request, pk):
    lead = _lead(pk)
    key, value = request.POST.get('key', ''), request.POST.get('value', '').strip()
    if key not in FACT_KEY_LABELS or len(value) < 3:
        messages.error(request, 'Choose what kind of information it is and describe it.')
        return _back(pk)
    bucket = request.POST.get('bucket', '')
    role = request.POST.get('role', '')
    if key == 'timeline' and bucket not in dict(TIMELINE_BUCKETS):
        messages.error(request, 'Choose the timeline range so the assistant can use it.')
        return _back(pk)
    services.add_manual_fact(lead, request.user, key, value, bucket if key == 'timeline' else '',
                             role if key == 'decision_role' else '', request.POST.get('note', ''))
    messages.success(request, 'Saved as verified information (entered by you).')
    return _back(pk)


@login_required
@block_view_only
@require_POST
def fact_action(request, pk, fid, action):
    fact = get_object_or_404(CustomerFact, pk=fid, lead_id=pk)
    if action == 'confirm' and fact.key == 'timeline' and not (fact.extra or {}).get('bucket'):
        b = request.POST.get('bucket', '')
        if b not in dict(TIMELINE_BUCKETS):
            messages.error(request, 'Choose the timeline range before confirming.')
            return _back(pk)
        fact.extra = {**(fact.extra or {}), 'bucket': b}
    if action == 'confirm' and fact.key == 'decision_role' and not (fact.extra or {}).get('role'):
        r = request.POST.get('role', '')
        if r not in dict(ROLE_CHOICES):
            messages.error(request, 'Choose the role before confirming.')
            return _back(pk)
        fact.extra = {**(fact.extra or {}), 'role': r}
    if services.set_fact_status(fact, request.user, action):
        messages.success(request, {'confirm': 'Confirmed - now counted as verified.', 'reject': 'Rejected - it will not come back.',
                                   'resolve': 'Marked as answered.', 'unresolve': 'Re-opened.'}[action])
    return _back(pk)


@login_required
@block_view_only
@require_POST
def question_action(request, pk, qid, action):
    q = get_object_or_404(QualificationQuestion, pk=qid, lead_id=pk)
    if services.mark_question(q, request.user, action):
        messages.success(request, {'asked': 'Marked as asked. The assistant will now wait for the answer instead of asking again.',
                                   'answered': 'Marked as answered - add the answer as verified information.',
                                   'dismissed': 'Question dismissed. It will not be suggested again.'}[action])
    return _back(pk)


# ── recommendation / next action ─────────────────────────────────────────────
@login_required
@block_view_only
@require_POST
def recommendation_accept(request, pk):
    lead = _lead(pk)
    if not getattr(lead, 'sales_insight', None) or not lead.sales_insight.recommendation:
        messages.error(request, 'There is no recommendation to accept.')
        return _back(pk)
    services.accept_recommendation(lead, request.user, owner=_user_or_none(request.POST.get('owner')),
                                   due_date=parse_date(request.POST.get('due_date', '') or ''))
    messages.success(request, 'Next action saved.')
    return _back(pk)


@login_required
@block_view_only
@require_POST
def recommendation_dismiss(request, pk):
    services.dismiss_recommendation(_lead(pk), request.user, request.POST.get('reason', ''))
    messages.success(request, 'Recommendation dismissed and recorded. Set your own next action below.')
    return _back(pk)


@login_required
@block_view_only
@require_POST
def next_action_set(request, pk):
    lead = _lead(pk)
    p = request.POST
    action = p.get('action', '').strip()
    if len(action) < 3:
        messages.error(request, 'Describe the next action.')
        return _back(pk)
    atype = p.get('action_type') if p.get('action_type') in dict(ACTION_TYPES) else 'other'
    services.set_next_action(lead, request.user, action_type=atype, action=action, owner=_user_or_none(p.get('owner')) or request.user,
                             due_date=parse_date(p.get('due_date', '') or ''), reason=p.get('reason', ''))
    messages.success(request, 'Next action saved.' + (' The change from the AI recommendation was recorded.'
                                                       if NextAction.objects.filter(lead=lead, status='open', is_override=True).exists() else ''))
    return _back(pk)


@login_required
@block_view_only
@require_POST
def next_action_done(request, pk):
    na = get_object_or_404(NextAction, pk=pk)
    services.complete_next_action(na, request.user)
    messages.success(request, 'Marked done. Decide the next step below.')
    return redirect(request.META.get('HTTP_REFERER') or 'salesai:dashboard')


@login_required
@block_view_only
@require_POST
def priority_decide(request, pk):
    level = request.POST.get('level', '')
    if level not in ('high', 'medium', 'low'):
        messages.error(request, 'Choose High, Medium or Low.')
        return _back(pk)
    lead = _lead(pk)
    services.decide_priority(lead, request.user, level, request.POST.get('reason', ''))
    messages.success(request, f'Priority set to {level}. This is your decision; the AI value is kept for comparison.')
    return _back(pk)


@login_required
@block_view_only
@require_POST
def close_out(request, pk):
    lead = _lead(pk)
    reason = request.POST.get('reason', '').strip()
    if len(reason) < 10:
        messages.error(request, 'Please record the reason (at least a short sentence).')
        return _back(pk)
    services.close_out_of_market(lead, request.user, reason)
    messages.success(request, 'Reason recorded and the lead was closed.')
    return _back(pk)


# ── call brief ───────────────────────────────────────────────────────────────
@login_required
def brief_view(request, pk):
    lead = _lead(pk)
    if request.method == 'POST':
        if request.user.is_authenticated:
            brief = generate_brief(lead, request.user, use_ai=SalesAssistantSettings.get().ai_enabled)
            messages.success(request, 'Call brief prepared. Please review it before contacting the customer.')
            return redirect('salesai:brief', pk=pk)
    brief = CallBrief.objects.filter(lead=lead).first()
    return render(request, 'salesai/brief.html', {'active': 'salesai', 'lead': lead, 'brief': brief,
                                                  'c': brief.content if brief else None, 'title': f'Call brief - {lead.name}'})


@login_required
@block_view_only
@require_POST
def brief_reviewed(request, pk):
    brief = get_object_or_404(CallBrief, pk=pk)
    brief.reviewed_by, brief.reviewed_at = request.user, timezone.now()
    brief.save()
    messages.success(request, 'Brief marked as reviewed.')
    return redirect('salesai:brief', pk=brief.lead_id)


# ── after the conversation ───────────────────────────────────────────────────
@login_required
@block_view_only
def after_call(request, pk):
    lead = _lead(pk)
    if request.method == 'POST':
        notes = request.POST.get('notes', '').strip()
        if len(notes) < 20:
            messages.error(request, 'Paste or type your notes from the call / meeting (a few sentences at least).')
        else:
            meeting = Meeting.objects.filter(pk=request.POST.get('meeting') or 0, lead=lead).first()
            when = parse_datetime(request.POST.get('occurred_at', '') or '') or timezone.now()
            if timezone.is_naive(when):
                when = timezone.make_aware(when)
            draft = postcall.create_draft(lead, request.user, notes, request.POST.get('comm_type', 'call'), when, meeting)
            return redirect('salesai:draft_review', pk=draft.pk)
    meetings = Meeting.objects.filter(lead=lead).order_by('-scheduled_at')[:10]
    return render(request, 'salesai/after_call.html', {'active': 'salesai', 'lead': lead, 'meetings': meetings,
                                                       'now': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
                                                       'comm_types': ConversationDraft.COMM_TYPES,
                                                       'title': f'After the conversation - {lead.name}'})


@login_required
def draft_review(request, pk):
    draft = get_object_or_404(ConversationDraft, pk=pk)
    d = draft.draft or {}
    return render(request, 'salesai/draft_review.html', {
        'active': 'salesai', 'draft': draft, 'd': d, 'lead': draft.lead, 'users': _users(), 'action_types': ACTION_TYPES,
        'lines': lambda items: '\n'.join(i['text'] for i in items or []),
        'req_lines': '\n'.join(i['text'] for i in d.get('confirmed_requirements', [])),
        'concern_lines': '\n'.join(i['text'] for i in d.get('customer_concerns', [])),
        'question_lines': '\n'.join(i['text'] for i in d.get('open_questions', [])),
        'title': f'Review call notes - {draft.lead.name}'})


def _lines(value):
    return [x.strip(' -•\t') for x in (value or '').splitlines() if x.strip(' -•\t')]


@login_required
@block_view_only
@require_POST
def draft_approve(request, pk):
    draft = get_object_or_404(ConversationDraft, pk=pk, status__in=['draft', 'ai_failed'])
    p = request.POST
    if not p.get('reviewed'):
        messages.error(request, 'Please tick "I have reviewed these notes" before saving them to the CRM.')
        return redirect('salesai:draft_review', pk=pk)
    summary = p.get('summary', '').strip()
    if len(summary) < 10:
        messages.error(request, 'The summary cannot be empty.')
        return redirect('salesai:draft_review', pk=pk)
    tasks = []
    for i in range(int(p.get('task_count', 0) or 0)):
        title = p.get(f'task_{i}_title', '').strip()
        if p.get(f'task_{i}_include') and title:
            tasks.append({'title': title, 'owner': _user_or_none(p.get(f'task_{i}_owner')),
                          'due': parse_date(p.get(f'task_{i}_due', '') or '')})
    ns_action = p.get('next_action', '').strip()
    data = {'summary': summary, 'requirements': _lines(p.get('requirements')), 'concerns': _lines(p.get('concerns')),
            'open_questions': _lines(p.get('open_questions')), 'decision_process': p.get('decision_process', '').strip(),
            'tasks': tasks,
            'next_step': {'action': ns_action, 'owner': _user_or_none(p.get('next_owner')) or request.user,
                          'due': parse_date(p.get('next_due', '') or ''),
                          'type': p.get('next_type') if p.get('next_type') in dict(ACTION_TYPES) else 'other',
                          'reason': p.get('next_reason', '')}}
    postcall.approve_draft(draft, request.user, data)
    messages.success(request, 'Approved. Notes, tasks and next action are now saved in the CRM.')
    return _back(draft.lead_id)


@login_required
@block_view_only
@require_POST
def draft_discard(request, pk):
    draft = get_object_or_404(ConversationDraft, pk=pk, status__in=['draft', 'ai_failed'])
    draft.status = 'discarded'
    draft.save()
    messages.success(request, 'Draft discarded. Nothing was saved to the CRM.')
    return _back(draft.lead_id)


# ── lists / dashboard ────────────────────────────────────────────────────────
def _qualified_open():
    active = Lead.objects.filter(is_duplicate=False).exclude(status__in=['lost', 'converted'])
    return LeadInsight.objects.filter(lead__in=active, qualification__qualified=True).select_related('lead')


@login_required
def dashboard(request):
    today = timezone.localdate()
    open_na = NextAction.objects.filter(status='open').select_related('lead', 'owner')
    have = set(open_na.values_list('lead_id', flat=True))
    qualified_no_action = [i for i in _qualified_open() if i.lead_id not in have]
    hot = LeadInsight.objects.filter(Q(priority__level='high') | Q(priority_final='high'),
                                     lead__is_duplicate=False).exclude(lead__status__in=['lost', 'converted']).select_related('lead')[:10]
    ctx = {
        'active': 'salesai', 'title': 'AI Sales Assistant',
        'mine': open_na.filter(owner=request.user)[:15],
        'overdue': open_na.filter(due_date__lt=today)[:15],
        'qualified_no_action': qualified_no_action[:15], 'qn_count': len(qualified_no_action),
        'hot': hot,
        'unreviewed_briefs': CallBrief.objects.filter(reviewed_at__isnull=True, lead__is_duplicate=False)
                            .exclude(lead__status__in=['lost', 'converted']).select_related('lead')[:10],
        'drafts': ConversationDraft.objects.filter(status='draft').select_related('lead')[:10],
        'pending': LeadInsight.objects.filter(ai_reviewed_at__isnull=True, lead__is_duplicate=False)
                   .exclude(lead__status__in=['lost', 'converted']).count(),
        'counts': {'open': open_na.count(), 'overdue': open_na.filter(due_date__lt=today).count(), 'mine': open_na.filter(owner=request.user).count()},
    }
    return render(request, 'salesai/dashboard.html', ctx)


@login_required
def next_actions(request):
    scope = request.GET.get('scope', 'mine')
    today = timezone.localdate()
    qs = NextAction.objects.filter(status='open').select_related('lead', 'owner', 'opportunity')
    have = set(qs.values_list('lead_id', flat=True))
    missing = []
    if scope == 'mine':
        qs = qs.filter(owner=request.user)
    elif scope == 'overdue':
        qs = qs.filter(due_date__lt=today)
    elif scope == 'missing':
        missing = [i for i in _qualified_open() if i.lead_id not in have]
        qs = qs.none()
    return render(request, 'salesai/next_actions.html', {'active': 'salesai', 'title': 'Next Actions', 'scope': scope,
                                                        'page': Paginator(qs, 50).get_page(request.GET.get('page')),
                                                        'missing': missing, 'today': today})


@login_required
def understanding(request):
    return render(request, 'salesai/understanding.html', {'active': 'salesai', 'title': 'Customer Understanding',
                                                          's': portfolio.summary(), 'labels': rules.FIELD_LABELS})


@login_required
@manager_only
def overrides(request):
    qs = OverrideLog.objects.select_related('lead', 'user')
    if request.GET.get('kind'):
        qs = qs.filter(kind=request.GET['kind'])
    acts = Counter(NextAction.objects.exclude(origin__in=['system']).values_list('origin', flat=True))
    return render(request, 'salesai/overrides.html', {
        'active': 'salesai', 'title': 'AI overrides', 'page': Paginator(qs, 50).get_page(request.GET.get('page')),
        'kinds': OverrideLog.KINDS, 'f': request.GET,
        'stats': {'accepted': acts.get('ai_accepted', 0), 'edited': acts.get('ai_edited', 0),
                  'human': acts.get('human', 0) + acts.get('post_conversation', 0)}})


# ── settings ─────────────────────────────────────────────────────────────────
@login_required
@manager_only
def assistant_settings(request):
    cfg = SalesAssistantSettings.get()
    if request.method == 'POST':
        p = request.POST
        for name in ('target_countries', 'target_industries'):
            setattr(cfg, name, p.get(name, ''))
        for name in ('near_term_days', 'followup_days_researching', 'question_wait_days', 'high_priority_score',
                     'medium_priority_score', 'min_known_factors', 'weight_need', 'weight_readiness',
                     'weight_authority', 'weight_deal_size', 'weight_effort', 'weight_fit'):
            if p.get(name, '').isdigit():
                setattr(cfg, name, int(p[name]))
        for name in ('ai_enabled', 'auto_analyze_new_leads', 'ask_budget_after_engagement'):
            setattr(cfg, name, name in p)
        cfg.updated_by = request.user
        cfg.save()
        messages.success(request, 'Settings saved.')
        return redirect('salesai:settings')
    return render(request, 'salesai/settings.html', {'active': 'salesai', 'title': 'AI Sales Assistant settings', 'cfg': cfg})


# ── Product / Solution Knowledge Base ─────────────────────────────────────────────────────────────────────
@login_required
def knowledge_base(request):
    return render(request, 'salesai/kb.html', {
        'active': 'salesai', 'title': 'Product / Solution Knowledge Base',
        'items': SISService.objects.all(), 'kinds': SISService.KINDS, 'efforts': EFFORT_CHOICES,
        'can_edit': request.user.is_superuser or getattr(getattr(request.user, 'profile', None), 'role', '') in ('admin', 'sales_manager', 'ceo'),
        'approved_count': SISService.objects.filter(status='approved', active=True).count()})


@login_required
@manager_only
@require_POST
def service_save(request):
    p = request.POST
    obj = SISService.objects.filter(pk=p.get('id') or 0).first() or SISService()
    if len(p.get('name', '').strip()) < 2:
        messages.error(request, 'Name is required.')
        return redirect('salesai:kb')
    before = {f: getattr(obj, f) for f in SISService.CONTENT_FIELDS} if obj.pk else {}
    obj.name = p['name'].strip()
    obj.kind = p.get('kind') if p.get('kind') in dict(SISService.KINDS) else 'product'
    for f in ('keywords', 'exclude_keywords', 'industries', 'description', 'features', 'pricing_note', 'timeline_note',
              'guarantees_note', 'case_examples', 'common_objections', 'discovery_questions'):
        setattr(obj, f, p.get(f, '').strip())
    obj.effort = p.get('effort') if p.get('effort') in dict(EFFORT_CHOICES) else 'unknown'
    obj.demo_available = 'demo_available' in p
    obj.active = 'active' in p
    changed = obj.pk and any(before[f] != getattr(obj, f) for f in SISService.CONTENT_FIELDS)
    if not obj.pk or changed:
        obj.status, obj.approved_by, obj.approved_at = 'draft', None, None     # any change needs a new approval
    obj.updated_by = request.user
    obj.save()
    messages.success(request, f'"{obj.name}" saved.' + (' It is a DRAFT and is not used for matching until a manager approves it.'
                     if obj.status == 'draft' else ''))
    return redirect('salesai:kb')


@login_required
@manager_only
@require_POST
def service_approve(request, pk):
    obj = get_object_or_404(SISService, pk=pk)
    if request.POST.get('action') == 'unapprove':
        obj.status, obj.approved_by, obj.approved_at = 'draft', None, None
        messages.success(request, f'"{obj.name}" moved back to draft.')
    else:
        if not obj.keyword_list():
            messages.error(request, 'Add at least one matching keyword before approving.')
            return redirect('salesai:kb')
        if len((obj.description or '').strip()) < 10:
            messages.error(request, 'Add an approved summary before approving - the assistant must have real text to show.')
            return redirect('salesai:kb')
        obj.status, obj.approved_by, obj.approved_at = 'approved', request.user, timezone.now()
        messages.success(request, f'"{obj.name}" approved. It can now be recommended.')
    obj.updated_by = request.user
    obj.save()
    return redirect('salesai:kb')


@login_required
@manager_only
@require_POST
def service_delete(request, pk):
    get_object_or_404(SISService, pk=pk).delete()
    messages.success(request, 'Entry deleted.')
    return redirect('salesai:kb')



@login_required
@block_view_only
@require_POST
def scope_approval_new(request, pk):
    lead = _lead(pk)
    approval = approval_mod.create_approval(lead, request.user)
    messages.success(request, 'Approval requested from Sales and Delivery.')
    return redirect('salesai:scope_approval_detail', pk=approval.pk)

@login_required
@block_view_only
@require_POST
def scope_approval_sales(request, pk):
    approval = get_object_or_404(ScopeApproval, pk=pk)
    ok, err = approval_mod.sales_approve(approval, request.user, request.POST.get('notes', ''))
    (messages.success if ok else messages.error)(request, 'Sales approved. Now waiting for Delivery.' if ok else err)
    return redirect('salesai:scope_approval_detail', pk=pk)

@login_required
@block_view_only
@require_POST
def scope_approval_delivery(request, pk):
    approval = get_object_or_404(ScopeApproval, pk=pk)
    ok, err = approval_mod.delivery_approve(approval, request.user, request.POST.get('notes', ''))
    (messages.success if ok else messages.error)(
        request, 'Delivery approved. The proposal may now be sent.' if ok else err)
    return redirect('salesai:scope_approval_detail', pk=pk)

@login_required
@block_view_only
@require_POST
def scope_approval_reject(request, pk):
    approval = get_object_or_404(ScopeApproval, pk=pk)
    ok, err = approval_mod.reject(approval, request.user, request.POST.get('reason', ''))
    (messages.error if ok else messages.error)(request, 'Rejected.' if ok else err)
    return redirect('salesai:scope_approval_detail', pk=pk)


@login_required
def scope_approval_detail(request, pk):
    from . import approval as approval_mod
    approval = get_object_or_404(ScopeApproval, pk=pk)
    return render(request, 'salesai/scope_approval.html', {
        'active': 'salesai', 'approval': approval, 'lead': approval.lead,
        'can_sales': approval_mod.can_approve_sales(request.user),
        'can_delivery': approval_mod.can_approve_delivery(request.user),
        'title': f'Scope approval - {approval.lead.name}',
    })