from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from leads.models import Company, CommunicationLog, Contact, Lead, Meeting, Opportunity, SalesPerson

from . import extraction, offers, rules, services
from .llm import AIUnavailable
from .models import (CallBrief, ConversationDraft, CustomerFact, LeadInsight, NextAction, OfferDecision, OverrideLog,
                     SISService)

User = get_user_model()


def kb(name, kind='product', approved=True, **kw):
    o = SISService.objects.create(name=name, kind=kind, status='approved' if approved else 'draft', **kw)
    return o


def seed_kb(approved=True):
    return dict(
        edu=kb('EduPilot', keywords='examination\nexam\nmulti campus\nschool management', industries='education\nschool',
               description='Approved EduPilot summary.', demo_available=True, approved=approved),
        crm=kb('SIS AI CRM', keywords='lead follow up\nfollow up\ncrm', description='Approved CRM summary.', demo_available=True, approved=approved),
        bdo=kb('AI BDO / Automation', keywords='automated follow up\nautomate lead\nfollow up\nautomation',
               description='Approved BDO summary.', approved=approved),
        cust=kb('Custom Software Development', kind='custom', keywords='custom software\nbespoke\nunique requirement',
                description='Approved custom summary.', approved=approved),
    )


def fake_ai(facts):
    """Patches the model call used by extraction so tests never touch the network."""
    return mock.patch('salesai.extraction.chat_json', return_value={'facts': facts})


@override_settings(SALESAI_BACKGROUND_ANALYSIS=False, CAPI_ENABLED=False)
class Base(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('boss', 'b@x.com', 'pw')
        self.rep = User.objects.create_user('rep', 'r@x.com', 'pw')
        self.sp = SalesPerson.objects.create(name='Rep', user=self.rep)
        self.client.force_login(self.admin)

    def make_lead(self, detail, name='Lead A', industry=None, **kw):
        lead = Lead.objects.create(name=name, contact_number='03001234567', detail=detail, country='Pakistan', spo=self.sp, **kw)
        if industry:
            co = Company.objects.create(name=name + ' Co', industry=industry)
            Contact.objects.create(first_name='X', lead=lead, company=co)
        return lead

    def review(self, lead, facts):
        with fake_ai(facts):
            return services.analyze_lead(lead, self.admin)

    def rec(self, lead):
        return LeadInsight.objects.get(lead=lead).recommendation


class OfferExamples(Base):
    def test_school_multicampus_exam_goes_to_edupilot_demo(self):
        seed_kb()
        text = 'We run a school with 4 campuses and our examination process is chaotic. We want to decide within 3 weeks.'
        lead = self.make_lead(text, industry='education')
        self.review(lead, [
            {'key': 'pain_point', 'value': 'examination process is chaotic across campuses', 'basis': 'stated',
             'source_ref': f'enquiry:{lead.pk}', 'evidence': 'our examination process is chaotic'},
            {'key': 'need', 'value': 'multi campus examination management', 'basis': 'stated',
             'source_ref': f'enquiry:{lead.pk}', 'evidence': 'school with 4 campuses'},
            {'key': 'timeline', 'value': 'decide within 3 weeks', 'basis': 'stated', 'bucket': 'immediate',
             'source_ref': f'enquiry:{lead.pk}', 'evidence': 'decide within 3 weeks'}])
        rec = self.rec(lead)
        self.assertEqual(rec['product']['label'], 'EduPilot')
        self.assertEqual(rec['action_type'], 'demo')
        match = LeadInsight.objects.get(lead=lead).offer_match
        self.assertEqual(match['status'], 'matched')
        self.assertTrue(any('examination' in w for w in match['why']))

    def test_lead_followup_automation_is_ambiguous_and_goes_to_discovery(self):
        seed_kb()
        lead = self.make_lead('We need automated follow up of our leads. We plan to start this month.')
        self.review(lead, [
            {'key': 'need', 'value': 'automated follow up of leads', 'basis': 'stated', 'source_ref': f'enquiry:{lead.pk}',
             'evidence': 'We need automated follow up of our leads'},
            {'key': 'timeline', 'value': 'start this month', 'basis': 'stated', 'bucket': 'immediate',
             'source_ref': f'enquiry:{lead.pk}', 'evidence': 'We plan to start this month'}])
        rec = self.rec(lead)
        self.assertEqual(rec['action_type'], 'discovery_meeting')
        self.assertIn('SIS AI CRM', rec['product']['label'])
        self.assertIn('AI BDO', rec['product']['label'])
        self.assertEqual(LeadInsight.objects.get(lead=lead).offer_match['status'], 'ambiguous')

    def test_unique_software_requirement_goes_to_custom_discovery(self):
        seed_kb()
        lead = self.make_lead('We have a unique requirement for a ship-tracking system. Need it by next month.')
        self.review(lead, [
            {'key': 'need', 'value': 'ship tracking system with a unique requirement', 'basis': 'stated',
             'source_ref': f'enquiry:{lead.pk}', 'evidence': 'unique requirement for a ship-tracking system'},
            {'key': 'timeline', 'value': 'needed by next month', 'basis': 'stated', 'bucket': 'immediate',
             'source_ref': f'enquiry:{lead.pk}', 'evidence': 'Need it by next month'}])
        rec = self.rec(lead)
        self.assertEqual(rec['product']['label'], 'Custom Software Discovery')
        self.assertEqual(rec['action_type'], 'discovery_meeting')

    def test_need_without_any_product_falls_back_to_custom_discovery(self):
        seed_kb()
        lead = self.make_lead('We want a system to manage our fleet of boats. Deciding this quarter.')
        self.review(lead, [
            {'key': 'need', 'value': 'system to manage a fleet of boats', 'basis': 'stated', 'source_ref': f'enquiry:{lead.pk}',
             'evidence': 'a system to manage our fleet of boats'},
            {'key': 'timeline', 'value': 'deciding this quarter', 'basis': 'stated', 'bucket': 'near',
             'source_ref': f'enquiry:{lead.pk}', 'evidence': 'Deciding this quarter'}])
        self.assertEqual(self.rec(lead)['product']['label'], 'Custom Software Discovery')

    def test_no_custom_offer_and_no_match_goes_to_human_review(self):
        e = seed_kb()
        e['cust'].delete()
        lead = self.make_lead('We want a system to manage our fleet of boats. Deciding this quarter.')
        self.review(lead, [
            {'key': 'need', 'value': 'system to manage a fleet of boats', 'basis': 'stated', 'source_ref': f'enquiry:{lead.pk}',
             'evidence': 'a system to manage our fleet of boats'},
            {'key': 'timeline', 'value': 'deciding this quarter', 'basis': 'stated', 'bucket': 'near',
             'source_ref': f'enquiry:{lead.pk}', 'evidence': 'Deciding this quarter'}])
        self.assertEqual(self.rec(lead)['action_type'], 'human_review')

    def test_matched_but_researching_gets_information_and_long_timeline_gets_nurture(self):
        seed_kb()
        for bucket, expected in (('researching', 'followup_info'), ('long', 'nurture')):
            lead = self.make_lead('School exam problem. Just researching options.', name=f'L{bucket}', industry='education')
            self.review(lead, [
                {'key': 'need', 'value': 'school exam problem', 'basis': 'stated', 'source_ref': f'enquiry:{lead.pk}', 'evidence': 'School exam problem'},
                {'key': 'timeline', 'value': 'just researching', 'basis': 'stated', 'bucket': bucket,
                 'source_ref': f'enquiry:{lead.pk}', 'evidence': 'Just researching options'}])
            self.assertEqual(self.rec(lead)['action_type'], expected, bucket)

    def test_industry_specific_offer_asks_industry_instead_of_guessing(self):
        seed_kb()
        lead = self.make_lead('We need to fix our examination results process. Decision this month.')
        self.review(lead, [
            {'key': 'need', 'value': 'fix examination results process', 'basis': 'stated', 'source_ref': f'enquiry:{lead.pk}',
             'evidence': 'fix our examination results process'},
            {'key': 'timeline', 'value': 'decision this month', 'basis': 'stated', 'bucket': 'immediate',
             'source_ref': f'enquiry:{lead.pk}', 'evidence': 'Decision this month'}])
        rec = self.rec(lead)
        self.assertEqual(rec['action_type'], 'ask_question')
        self.assertEqual(rec['question']['field'], 'industry')


class NoInvention(Base):
    def test_draft_products_are_never_recommended(self):
        seed_kb(approved=False)
        lead = self.make_lead('School with exam problems. Decide this month.', industry='education')
        self.review(lead, [
            {'key': 'need', 'value': 'exam problems', 'basis': 'stated', 'source_ref': f'enquiry:{lead.pk}', 'evidence': 'School with exam problems'},
            {'key': 'timeline', 'value': 'decide this month', 'basis': 'stated', 'bucket': 'immediate',
             'source_ref': f'enquiry:{lead.pk}', 'evidence': 'Decide this month'}])
        rec = self.rec(lead)
        self.assertEqual(rec['action_type'], 'human_review')
        self.assertEqual(rec['product']['label'], '')

    def test_fabricated_quote_is_only_an_assumption_and_gives_no_recommendation(self):
        seed_kb()
        lead = self.make_lead('Please call me back about your services.')
        self.review(lead, [{'key': 'need', 'value': 'wants exam management', 'basis': 'stated', 'source_ref': f'enquiry:{lead.pk}',
                            'evidence': 'we need exam management for 5 campuses'}])
        fact = CustomerFact.objects.get(lead=lead, key='need')
        self.assertEqual(fact.status, 'assumed')
        self.assertNotEqual(self.rec(lead)['product']['label'], 'EduPilot')
        self.assertFalse(LeadInsight.objects.get(lead=lead).qualification['qualified'])

    def test_assumed_need_shows_only_a_tentative_hint_never_a_recommendation(self):
        seed_kb()
        lead = self.make_lead('Maybe something for exams. Not sure.', industry='education')
        self.review(lead, [{'key': 'need', 'value': 'exam management', 'basis': 'inferred', 'source_ref': f'enquiry:{lead.pk}',
                            'evidence': 'Maybe something for exams'}])
        match = LeadInsight.objects.get(lead=lead).offer_match
        self.assertEqual(match['status'], 'no_need')
        self.assertEqual(match['tentative']['name'], 'EduPilot')
        self.assertEqual(self.rec(lead)['product']['label'], '')

    def test_numbers_and_hedges_block_verification(self):
        src = mock.Mock(text='Maybe we have 10 campuses', speaker='customer', label='x')
        out = extraction.validate_facts([{'key': 'scale', 'value': '10 campuses', 'basis': 'stated', 'source_ref': 'r', 'evidence': 'Maybe we have 10 campuses'}], {'r': src})
        self.assertEqual(out[0]['status'], 'assumed')
        src2 = mock.Mock(text='We have 10 campuses', speaker='customer', label='x')
        out = extraction.validate_facts([{'key': 'scale', 'value': '12 campuses', 'basis': 'stated', 'source_ref': 'r', 'evidence': 'We have 10 campuses'}], {'r': src2})
        self.assertEqual(out[0]['status'], 'assumed')
        out = extraction.validate_facts([{'key': 'scale', 'value': '10 campuses', 'basis': 'stated', 'source_ref': 'r', 'evidence': 'We have 10 campuses'}], {'r': src2})
        self.assertEqual(out[0]['status'], 'verified')

    def test_brief_never_states_missing_features_pricing_or_timelines(self):
        seed_kb()
        lead = self.make_lead('School exam problem. Decide this month.', industry='education')
        self.review(lead, [
            {'key': 'need', 'value': 'school exam problem', 'basis': 'stated', 'source_ref': f'enquiry:{lead.pk}', 'evidence': 'School exam problem'},
            {'key': 'timeline', 'value': 'decide this month', 'basis': 'stated', 'bucket': 'immediate',
             'source_ref': f'enquiry:{lead.pk}', 'evidence': 'Decide this month'}])
        with mock.patch('salesai.brief.chat_json', side_effect=AIUnavailable('off')):
            from .brief import generate_brief
            b = generate_brief(lead, self.admin)
        sol = b.content['relevant_solutions'][0]
        self.assertEqual(sol['pricing_note'], '')
        self.assertEqual(sol['timeline_note'], '')
        self.assertEqual(sol['features'], [])
        self.assertTrue(any('not in the knowledge base' in w for w in b.content['warnings']))
        page = self.client.get(reverse('salesai:brief', args=[lead.pk])).content.decode()
        self.assertIn('not in the knowledge base - do not quote', page)

    def test_ai_outage_still_gives_rules_based_result(self):
        seed_kb()
        lead = self.make_lead('School exam problem.')
        with mock.patch('salesai.extraction.chat_json', side_effect=AIUnavailable('down')):
            summary, insight = services.analyze_lead(lead, self.admin)
        self.assertFalse(summary['ai'])
        self.assertIn('down', insight.ai_error)
        self.assertTrue(insight.recommendation)


class SalesDecision(Base):
    def prepare(self):
        seed_kb()
        lead = self.make_lead('School exam problem. Decide this month.', industry='education')
        self.review(lead, [
            {'key': 'need', 'value': 'school exam problem', 'basis': 'stated', 'source_ref': f'enquiry:{lead.pk}', 'evidence': 'School exam problem'},
            {'key': 'timeline', 'value': 'decide this month', 'basis': 'stated', 'bucket': 'immediate',
             'source_ref': f'enquiry:{lead.pk}', 'evidence': 'Decide this month'}])
        return lead

    def test_accept_records_decision_and_next_action(self):
        lead = self.prepare()
        r = self.client.post(reverse('salesai:offer_decide', args=[lead.pk]), {'choice': 'accept', 'owner': self.rep.pk})
        self.assertEqual(r.status_code, 302)
        d = OfferDecision.objects.get(lead=lead)
        self.assertEqual((d.decision, d.chosen_product_label, d.chosen_action_type), ('accepted', 'EduPilot', 'demo'))
        na = NextAction.objects.get(lead=lead, status='open')
        self.assertEqual((na.origin, na.owner_id, na.is_override), ('ai_accepted', self.rep.pk, False))
        self.assertTrue(na.due_date)

    def test_override_requires_reason_and_only_approved_products(self):
        lead = self.prepare()
        url = reverse('salesai:offer_decide', args=[lead.pk])
        self.client.post(url, {'choice': 'override', 'product': 'none', 'action_type': 'nurture', 'reason': ''})
        self.assertFalse(OfferDecision.objects.filter(lead=lead).exists())
        draft = SISService.objects.create(name='Vapourware', status='draft')
        self.client.post(url, {'choice': 'override', 'product': draft.pk, 'action_type': 'demo', 'reason': 'customer asked for it explicitly'})
        self.assertFalse(OfferDecision.objects.filter(lead=lead).exists())
        crm = SISService.objects.get(name='SIS AI CRM')
        self.client.post(url, {'choice': 'override', 'product': crm.pk, 'action_type': 'discovery_meeting',
                               'reason': 'Customer already uses our CRM and wants an add-on', 'owner': self.rep.pk})
        d = OfferDecision.objects.get(lead=lead)
        self.assertEqual((d.decision, d.chosen_product_label, d.chosen_action_type, d.ai_product_label), ('overridden', 'SIS AI CRM', 'discovery_meeting', 'EduPilot'))
        self.assertIn('already uses', d.reason)
        log = OverrideLog.objects.get(lead=lead, kind='offer')
        self.assertIn('product', log.changed_fields)
        na = NextAction.objects.get(lead=lead, status='open')
        self.assertTrue(na.is_override)
        self.assertEqual(na.action_type, 'discovery_meeting')

    def test_decision_marked_stale_when_ai_recommendation_changes(self):
        lead = self.prepare()
        self.client.post(reverse('salesai:offer_decide', args=[lead.pk]), {'choice': 'accept'})
        self.assertFalse(self.client.get(reverse('lead_detail', args=[lead.pk])).context is None)
        CustomerFact.objects.filter(lead=lead, key='timeline').update(extra={'bucket': 'long'})
        services.recompute_rules(lead)
        page = self.client.get(reverse('lead_detail', args=[lead.pk])).content.decode()
        self.assertIn('has changed since this decision', page)

    def test_lead_and_opportunity_screens_show_the_four_sections(self):
        lead = self.prepare()
        opp = Opportunity.objects.create(title='School deal', lead=lead)
        for url in (reverse('lead_detail', args=[lead.pk]), reverse('opportunity_detail', args=[opp.pk])):
            page = self.client.get(url).content.decode()
            for label in ('Recommended Product', 'Why Matched', 'Missing Information', 'Recommended Next Action', 'EduPilot'):
                self.assertIn(label, page, (url, label))
            self.assertIn('Accept recommendation', page)

    def test_view_only_and_kb_permissions(self):
        seed_kb(approved=False)
        self.client.force_login(self.rep)             # no manager role: cannot approve
        s = SISService.objects.get(name='EduPilot')
        self.client.post(reverse('salesai:service_approve', args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual(s.status, 'draft')

    def test_kb_approval_needs_content_and_edits_reset_approval(self):
        s = SISService.objects.create(name='Xy', keywords='xy', description='')
        self.client.post(reverse('salesai:service_approve', args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual(s.status, 'draft')                      # no approved summary -> cannot approve
        s.description = 'Approved text here.'; s.save()
        self.client.post(reverse('salesai:service_approve', args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual((s.status, s.approved_by_id), ('approved', self.admin.pk))
        self.client.post(reverse('salesai:service_save'), {'id': s.pk, 'name': 'Xy', 'kind': 'product', 'keywords': 'xy',
                                                          'description': 'Edited text', 'active': 'on'})
        s.refresh_from_db()
        self.assertEqual(s.status, 'draft')                      # edit -> needs a new approval


class OtherAssistantParts(Base):
    def test_seed_command_creates_only_unapproved_drafts_with_no_customer_facing_text(self):
        from django.core.management import call_command
        call_command('salesai_seed_kb', stdout=mock.MagicMock())
        names = set(SISService.objects.values_list('name', flat=True))
        self.assertTrue({'EduPilot', 'SIS AI CRM', 'AI BDO / Automation', 'Custom Software Development'} <= names)
        for s in SISService.objects.all():
            self.assertEqual((s.status, s.description, s.features, s.pricing_note, s.timeline_note, s.guarantees_note), ('draft', '', '', '', '', ''))
        lead = self.make_lead('School exam problem. Decide this month.', industry='education')
        self.review(lead, [{'key': 'need', 'value': 'school exam problem', 'basis': 'stated', 'source_ref': f'enquiry:{lead.pk}', 'evidence': 'School exam problem'}])
        self.assertEqual(self.rec(lead)['product']['label'], '')     # drafts are inert until approved

    def test_new_lead_gets_insight_immediately_without_ai(self):
        with self.captureOnCommitCallbacks(execute=True):
            lead = Lead.objects.create(name='New', contact_number='0300111', detail='hello')
        self.assertTrue(LeadInsight.objects.filter(lead=lead).exists())

    def test_priority_not_recommended_without_enough_verified_information(self):
        seed_kb()
        lead = self.make_lead('School exam problem.')
        self.review(lead, [{'key': 'need', 'value': 'school exam problem', 'basis': 'stated', 'source_ref': f'enquiry:{lead.pk}', 'evidence': 'School exam problem'}])
        self.assertEqual(LeadInsight.objects.get(lead=lead).priority['level'], 'insufficient')

    def test_post_call_draft_saves_nothing_until_approved(self):
        seed_kb()
        lead = self.make_lead('School exam problem.')
        notes = 'Spoke to the principal. They need results for 3 campuses. Rep will send a proposal by 2026-10-05.'
        raw = {'summary': 'Principal wants results for 3 campuses.', 'confirmed_requirements': [
            {'text': 'results for 3 campuses', 'evidence': 'need results for 3 campuses'},
            {'text': 'integration with Oracle', 'evidence': 'needs Oracle integration'}],
            'agreed_next_step': {'text': 'send a proposal', 'evidence': 'will send a proposal'}, 'follow_up_tasks': []}
        with mock.patch('salesai.postcall.chat_json', return_value=raw):
            draft = __import__('salesai.postcall', fromlist=['x']).create_draft(lead, self.admin, notes)
        self.assertEqual(len(draft.draft['confirmed_requirements']), 1)          # invented Oracle item dropped
        self.assertTrue(any('Oracle' in u['text'] for u in draft.draft['unsupported']))
        self.assertFalse(CommunicationLog.objects.filter(lead=lead, subject__contains='approved').exists())
        self.assertFalse(CustomerFact.objects.filter(lead=lead, origin='post_call').exists())
        r = self.client.post(reverse('salesai:draft_approve', args=[draft.pk]), {'summary': 'Principal wants results.', 'requirements': 'results for 3 campuses'})
        self.assertFalse(CommunicationLog.objects.filter(lead=lead, subject__contains='approved').exists())   # not reviewed -> nothing saved
        self.client.post(reverse('salesai:draft_approve', args=[draft.pk]), {
            'reviewed': 'on', 'summary': 'Principal wants results for 3 campuses.', 'requirements': 'results for 3 campuses',
            'next_action': 'Send proposal', 'next_owner': self.rep.pk, 'next_due': '2026-10-05', 'next_type': 'send_proposal', 'task_count': 0})
        self.assertTrue(CommunicationLog.objects.filter(lead=lead, subject__contains='approved').exists())
        self.assertTrue(CustomerFact.objects.filter(lead=lead, origin='post_call', key='requirement', status='verified').exists())
        self.assertEqual(NextAction.objects.get(lead=lead, status='open').owner_id, self.rep.pk)

    def test_all_pages_render(self):
        seed_kb()
        lead = self.make_lead('School exam problem.', industry='education')
        services.recompute_rules(lead)
        for name, args in (('dashboard', []), ('next_actions', []), ('understanding', []), ('overrides', []), ('settings', []),
                           ('kb', []), ('workspace', [lead.pk]), ('brief', [lead.pk]), ('after_call', [lead.pk])):
            r = self.client.get(reverse(f'salesai:{name}', args=args))
            self.assertEqual(r.status_code, 200, name)
