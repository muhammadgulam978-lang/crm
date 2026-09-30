@transaction.atomic
def approve_draft(draft, user, *, title, sections, next_step=None):
    """
    `sections`: {section_key: [lines]} - the salesperson's REVIEWED text for the customer-facing sections.
    `next_step`: optional {'owner': User, 'due': date} -> sets "send the proposal" as the next action.
    Returns the leads.Proposal that was created (status "draft": approving the structure does not send anything).
    """
    from leads.models import Proposal
    # Lazy import - avoids the circular import with approval.py
    from .approval import create_approval, gate_ok

    title = _clean(title)[:200] or draft.content.get('title', 'Business Proposal')
    ordered = [(SECTION_TITLES[k], [_clean(x) for x in sections.get(k, [])]) for k, _ in CUSTOMER_SECTIONS]
    text = render_text(title, ordered)
    open_items = len(PLACEHOLDER.findall(text))
    proposal = Proposal.objects.create(lead=draft.lead, title=title, generated_proposal=text, status='draft', created_by=user)
    draft.final = {'title': title, 'sections': {k: [_clean(x) for x in v if _clean(x)] for k, v in sections.items()},
                   'open_placeholders': open_items}
    draft.status, draft.approved_by, draft.approved_at, draft.crm_proposal_id = 'approved', user, timezone.now(), proposal.pk
    draft.save()
    if next_step:
        set_next_action(draft.lead, user, action_type='send_proposal',
                        action='Finalise the proposal' + (f' ({open_items} item(s) still to confirm)' if open_items else '') +
                               ' and send it to the customer',
                        owner=next_step.get('owner') or user, due_date=next_step.get('due'), ai_rec={},
                        opportunity=draft.opportunity)
    _mirror_activity(user, draft.lead, f'Proposal structure approved and saved to Proposals ({open_items} item(s) still to confirm)')
    recompute_rules(draft.lead, user)
    return proposal