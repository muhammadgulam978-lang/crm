"""
Feature 1: Sales + Delivery approval gate.

RULE: A proposal, quotation, SOW or any customer-facing commitment that contains scope, features,
timeline, price, assumptions or dependencies CANNOT be sent until BOTH Sales and Delivery have approved
the exact snapshot of those six items.

The AI may DRAFT the snapshot (from verified facts + approved KB), but it can never approve it.
Only a user with role 'admin' / 'sales_manager' / 'ceo' can approve as Sales,
and only a user with role 'admin' / 'delivery_manager' / 'ceo' (or in DELIVERY_APPROVER_GROUP) as Delivery.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import ScopeApproval

from .services import _mirror_activity, primary_opportunity

User = get_user_model()

SALES_ROLES = {'admin', 'sales_manager', 'ceo'}
DELIVERY_ROLES = {'admin', 'delivery_manager', 'ceo', 'ops_manager'}


def _role(user):
    return getattr(getattr(user, 'profile', None), 'role', '') or ''


def can_approve_sales(user):
    return user.is_superuser or _role(user) in SALES_ROLES


def can_approve_delivery(user):
    if user.is_superuser or _role(user) in DELIVERY_ROLES:
        return True
    group = getattr(settings, 'SALESAI_DELIVERY_APPROVER_GROUP', '')
    return bool(group and user.groups.filter(name=group).exists())

def build_snapshot(lead, proposal_draft=None, service=None):
    """
    Build the six-item snapshot for approval. Pure rules, no AI.
    Source of each item is explicit so both approvers can see where it came from.
    """
    # Lazy imports - avoid circular import with proposal.py
    from .proposal import _verified, resolve_offer
    from .models import ASSUMED, CustomerFact
    from .services import service_catalogue

    insight = getattr(lead, 'sales_insight', None)
    svc, _, _ = resolve_offer(lead, insight) if not service else (service, '', '')
    sections = {}
    if proposal_draft and proposal_draft.final:
        sections = proposal_draft.final.get('sections', {}) or {}
    elif proposal_draft and proposal_draft.content:
        sections = {s['key']: [i['text'] for i in s['items'] if not s['internal']]
                    for s in proposal_draft.content.get('sections', []) if not s['internal']}

    features = (svc.feature_list() if svc else [])
    scope_lines = sections.get('scope', [])
    in_scope = [x for x in scope_lines if 'TO CONFIRM' not in x]
    out_of_scope = [x for x in sections.get('requirements', []) if x and 'TO CONFIRM' in x]

    timeline = {
        'statement': (svc.timeline_note if svc and svc.timeline_note else ''),
        'customer_target': next((f.value for f in _verified(lead, 'timeline')[:1]), ''),
        'sections': sections.get('timeline', []),
    }
    price = {
        'statement': (svc.pricing_note if svc and svc.pricing_note else ''),
        'sections': sections.get('investment', []),
    }
    assumptions = [f'{f.get_key_display()}: {f.value}' for f in
                   CustomerFact.objects.filter(lead=lead, status=ASSUMED)]
    assumptions += [x for v in sections.values() for x in v if 'TO CONFIRM' in x]
    dependencies = []
    return {
        'scope': {'in_scope': in_scope, 'out_of_scope': out_of_scope},
        'features': features,
        'timeline': timeline,
        'price': price,
        'assumptions': assumptions,
        'dependencies': dependencies,
        'exclusions': sections.get('exclusions', []),
    }


def create_approval(lead, user, *, proposal_draft=None, service=None, overrides=None):
    """
    Create a PENDING approval request. `overrides` may replace any of the six items.
    Only one live approval per lead: previous pending/superseded ones are marked 'superseded'.
    """
    snap = build_snapshot(lead, proposal_draft, service)
    for k, v in (overrides or {}).items():
        snap[k] = v
    ScopeApproval.objects.filter(lead=lead, status__in=['pending_sales', 'pending_delivery', 'approved']).update(status='superseded')
    approval = ScopeApproval.objects.create(
        lead=lead, opportunity=primary_opportunity(lead), proposal_draft=proposal_draft,
        created_by=user, status='pending_sales', **snap)
    _mirror_activity(user, lead, 'Scope approval requested (Sales + Delivery) before any customer commitment.')
    return approval


def sales_approve(approval, user, notes=''):
    if not can_approve_sales(user):
        return False, 'Only Sales (manager / admin / CEO) can approve the sales side.'
    if approval.status != 'pending_sales':
        return False, 'This approval is not waiting for Sales.'
    approval.sales_approved_by, approval.sales_approved_at = user, timezone.now()
    approval.sales_notes = notes[:2000]
    approval.status = 'pending_delivery'
    approval.save()
    _mirror_activity(user, approval.lead, 'Scope approved by Sales - now waiting for Delivery.')
    return True, ''


def delivery_approve(approval, user, notes=''):
    if not can_approve_delivery(user):
        return False, 'Only Delivery (delivery manager / admin / CEO) can approve the delivery side.'
    if approval.status != 'pending_delivery':
        return False, 'This approval is not waiting for Delivery.'
    approval.delivery_approved_by, approval.delivery_approved_at = user, timezone.now()
    approval.delivery_notes = notes[:2000]
    approval.status = 'approved'
    approval.save()
    _mirror_activity(user, approval.lead, 'Scope APPROVED by Sales + Delivery. Proposal may now be sent.')
    # if this approval is tied to a ProposalDraft, mark the draft "sendable"
    if approval.proposal_draft_id:
        from .models import ProposalDraft
        ProposalDraft.objects.filter(pk=approval.proposal_draft_id, status='approved').update(
            commercial_approved=True)
    return True, ''


def reject(approval, user, reason):
    if not (can_approve_sales(user) or can_approve_delivery(user)):
        return False, 'Only Sales or Delivery approvers can reject.'
    approval.status = 'rejected'
    approval.rejection_reason = reason[:500]
    approval.save()
    _mirror_activity(user, approval.lead, f'Scope approval REJECTED: {reason}')
    return True, ''


def gate_ok(lead):
    """True only when the LATEST approval for the lead is fully approved."""
    latest = ScopeApproval.objects.filter(lead=lead).order_by('-created_at').first()
    return bool(latest and latest.is_fully_approved), latest