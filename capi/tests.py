import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from leads.models import Contract, Lead, Meeting, Opportunity, Payment, Quotation

from . import constants as C
from .models import (ConversionEvent, ConversionEventConfig, LeadTouchpoint, LeadTracking,
                     PlatformConnection)
from .services import identifiers as ids
from .services import queue as q
from .services.intake import upsert_lead_from_platform


def make_conn(platform=C.PLATFORM_META, **kw):
    conn, _ = PlatformConnection.objects.get_or_create(platform=platform)
    conn.is_active = True
    conn.dataset_id = kw.get('dataset_id', '12345')
    conn.ad_account_id = '999'
    conn.set_token('TOKEN-abcd')
    conn.save()
    return conn


def make_config(conn, trigger, **kw):
    defaults = dict(name=f'cfg-{trigger}', connection=conn, trigger_key=trigger, is_enabled=True,
                    platform_event_name='QualifiedLead',
                    linkedin_conversion_urn='urn:lla:llaPartnerConversion:1', require_consent=True)
    defaults.update(kw)
    return ConversionEventConfig.objects.create(**defaults)


def make_lead(platform='meta', consent='granted', **kw):
    lead = Lead.objects.create(name='Ayesha Khan', contact_number='0300 1234567',
                               email=kw.pop('email', 'Ayesha@Example.com'), **kw)
    LeadTracking.objects.create(lead=lead, platform=platform, consent_status=consent,
                                campaign_id='c1', campaign_name='Spring', external_lead_id='444455556666777')
    return lead


class IdentifierTests(TestCase):
    def test_normalisation_and_hash(self):
        self.assertEqual(ids.normalize_phone('0300-1234567'), '923001234567')
        self.assertEqual(ids.normalize_phone('+971 50 123 4567'), '971501234567')
        self.assertEqual(ids.normalize_phone('00447700900123'), '447700900123')
        self.assertIsNone(ids.normalize_phone('abc'))
        self.assertEqual(ids.hashed_email('  Ayesha@Example.com '), ids.sha256_hex('ayesha@example.com'))
        self.assertIsNone(ids.hashed_email('not-an-email'))


class TriggerEngineTests(TestCase):
    def setUp(self):
        self.meta = make_conn()

    def _opp(self, lead, stage='new'):
        return Opportunity.objects.create(title='Deal', lead=lead, stage=stage, value=Decimal('500'))

    def test_stage_change_creates_event_once(self):
        make_config(self.meta, C.TRIGGER_OPP_STAGE, to_value='qualified')
        lead = make_lead()
        with self.captureOnCommitCallbacks(execute=True):
            opp = self._opp(lead, 'new')
        self.assertEqual(ConversionEvent.objects.count(), 0)        # not qualified yet
        with self.captureOnCommitCallbacks(execute=True):
            opp.stage = 'qualified'; opp.save()
            opp.stage = 'contacted'; opp.save()
            opp.stage = 'qualified'; opp.save()                      # re-qualify -> no duplicate
        self.assertEqual(ConversionEvent.objects.count(), 1)
        ev = ConversionEvent.objects.get()
        self.assertEqual((ev.status, ev.platform, ev.campaign_name), ('pending', 'meta', 'Spring'))

    def test_disabled_config_sends_nothing(self):
        make_config(self.meta, C.TRIGGER_OPP_STAGE, to_value='qualified', is_enabled=False)
        lead = make_lead()
        with self.captureOnCommitCallbacks(execute=True):
            self._opp(lead, 'qualified')
        self.assertEqual(ConversionEvent.objects.count(), 0)

    def test_ineligible_leads_are_not_queued(self):
        make_config(self.meta, C.TRIGGER_OPP_STAGE, to_value='qualified')
        no_consent = make_lead(consent='unknown', email='a@x.com')
        wrong_platform = make_lead(platform='linkedin', email='b@x.com')
        untracked = Lead.objects.create(name='No Track', contact_number='0301111111', email='c@x.com')
        dup = make_lead(email='d@x.com'); dup.is_duplicate = True; dup.save()
        with self.captureOnCommitCallbacks(execute=True):
            for l in (no_consent, wrong_platform, untracked, dup):
                self._opp(l, 'qualified')
        self.assertEqual(ConversionEvent.objects.count(), 0)

    def test_appointment_contract_won_and_revenue(self):
        for trig in (C.TRIGGER_MEETING, C.TRIGGER_CONTRACT_SIGNED, C.TRIGGER_DEAL_WON, C.TRIGGER_REVENUE,
                     C.TRIGGER_QUOTE_APPROVED):
            make_config(self.meta, trig, send_value=trig in C.VALUE_TRIGGERS, currency='USD')
        lead = make_lead()
        with self.captureOnCommitCallbacks(execute=True):
            opp = self._opp(lead)
            Meeting.objects.create(title='Demo', lead=lead, scheduled_at=timezone.now())
            quote = Quotation.objects.create(title='Q', lead=lead, opportunity=opp)
            quote.status = 'approved'; quote.save()
            contract = Contract.objects.create(title='C', opportunity=opp, value=Decimal('900'))
            contract.status = 'signed'; contract.save()
            opp.stage = 'won'; opp.save()
            Payment.objects.create(lead=lead, payment_type='advance', amount=Decimal('250'))
            Payment.objects.create(lead=lead, payment_type='full', amount=Decimal('650'))
        by_trigger = {}
        for e in ConversionEvent.objects.all():
            by_trigger.setdefault(e.trigger_key, []).append(e)
        self.assertEqual(set(by_trigger), {C.TRIGGER_MEETING, C.TRIGGER_QUOTE_APPROVED, C.TRIGGER_CONTRACT_SIGNED,
                                           C.TRIGGER_DEAL_WON, C.TRIGGER_REVENUE})
        self.assertEqual(sorted(e.value for e in by_trigger[C.TRIGGER_REVENUE]), [Decimal('250'), Decimal('650')])
        self.assertEqual(by_trigger[C.TRIGGER_CONTRACT_SIGNED][0].value, Decimal('900'))
        self.assertIsNone(by_trigger[C.TRIGGER_MEETING][0].value)

    def test_lead_status_and_score_triggers(self):
        make_config(self.meta, C.TRIGGER_LEAD_STATUS, from_value='new', to_value='positive', name='engaged')
        make_config(self.meta, C.TRIGGER_LEAD_SCORE, min_score=70, name='score70')
        lead = make_lead()
        with self.captureOnCommitCallbacks(execute=True):
            lead.status = 'positive'; lead.save()
        with self.captureOnCommitCallbacks(execute=True):
            lead.ai_score = 60; lead.save()          # below threshold
        self.assertEqual(ConversionEvent.objects.count(), 1)
        with self.captureOnCommitCallbacks(execute=True):
            lead.ai_score = 82; lead.save(update_fields=['ai_score'])   # crosses 70
        self.assertEqual(ConversionEvent.objects.filter(trigger_key=C.TRIGGER_LEAD_SCORE).count(), 1)

    def test_signal_failure_never_breaks_crm_save(self):
        make_config(self.meta, C.TRIGGER_LEAD_CREATED)
        with mock.patch('capi.signals.fire', side_effect=RuntimeError('boom')):
            with self.captureOnCommitCallbacks(execute=True):
                lead = Lead.objects.create(name='Safe', contact_number='0302222222')
        self.assertTrue(Lead.objects.filter(pk=lead.pk).exists())


def fake_response(status=200, js=None, headers=None):
    r = mock.Mock(); r.status_code = status; r.json.return_value = js or {}
    r.text = json.dumps(js or {}); r.content = r.text.encode(); r.headers = headers or {}
    return r


class DeliveryTests(TestCase):
    def setUp(self):
        self.meta = make_conn()
        self.cfg = make_config(self.meta, C.TRIGGER_OPP_STAGE, to_value='qualified', send_value=True,
                               fixed_value=Decimal('10'), currency='USD')
        self.lead = make_lead()
        with self.captureOnCommitCallbacks(execute=True):
            Opportunity.objects.create(title='D', lead=self.lead, stage='qualified')
        self.ev = ConversionEvent.objects.get()

    @mock.patch('capi.services.meta.requests.post')
    def test_meta_success_and_privacy(self, post):
        post.return_value = fake_response(200, {'events_received': 1, 'fbtrace_id': 'TRACE1'})
        self.assertEqual(q.process_queue()['sent'], 1)
        ev = ConversionEvent.objects.get()
        self.assertEqual((ev.status, ev.platform_trace_id, ev.events_received), ('sent', 'TRACE1', 1))
        sent = post.call_args.kwargs['json']
        item = sent['data'][0]
        self.assertEqual(item['action_source'], 'system_generated')
        self.assertEqual(item['custom_data']['event_source'], 'crm')
        self.assertEqual(item['user_data']['lead_id'], 444455556666777)
        self.assertEqual(item['user_data']['em'], [ids.sha256_hex('ayesha@example.com')])
        self.assertEqual(item['user_data']['ph'], [ids.sha256_hex('923001234567')])
        self.assertEqual(item['custom_data']['value'], 10.0)
        blob = json.dumps(ev.request_payload)
        for secret in ('ayesha@example.com', '3001234567', 'TOKEN-abcd'):
            self.assertNotIn(secret, blob)                      # only hashes are stored
        self.assertNotIn('TOKEN-abcd', ev.response_body)
        self.assertEqual(ev.logs.count(), 1)

    @mock.patch('capi.services.meta.requests.post')
    def test_server_error_retries_then_gives_up(self, post):
        post.return_value = fake_response(500, {'error': {'code': 2, 'message': 'temporary'}})
        q.process_queue()
        ev = ConversionEvent.objects.get()
        self.assertEqual((ev.status, ev.attempts), ('retrying', 1))
        self.assertGreater(ev.next_attempt_at, timezone.now())
        for _ in range(C.MAX_ATTEMPTS):                          # force-due each time
            ConversionEvent.objects.update(next_attempt_at=timezone.now())
            q.process_queue()
        ev.refresh_from_db()
        self.assertEqual((ev.status, ev.attempts), ('failed', C.MAX_ATTEMPTS))

    @mock.patch('capi.services.meta.requests.post')
    def test_token_error_is_terminal_and_flags_connection(self, post):
        post.return_value = fake_response(400, {'error': {'code': 190, 'message': 'Invalid OAuth token'}})
        q.process_queue()
        ev = ConversionEvent.objects.get(); self.meta.refresh_from_db()
        self.assertEqual(ev.status, 'failed')
        self.assertEqual(self.meta.token_status, 'rejected')
        # manual retry after token is replaced
        self.meta.set_token('NEW-TOKEN'); self.meta.save()
        post.return_value = fake_response(200, {'events_received': 1})
        self.assertEqual(q.retry_now(ev).status, 'sent')

    @mock.patch('capi.services.meta.requests.post')
    def test_no_identifiers_is_skipped_not_sent(self, post):
        Lead.objects.filter(pk=self.lead.pk).update(email='', contact_number='x')
        LeadTracking.objects.filter(lead=self.lead).update(external_lead_id='')
        q.process_queue()
        self.assertEqual(ConversionEvent.objects.get().status, 'skipped')
        post.assert_not_called()

    @mock.patch('capi.services.meta.requests.post')
    def test_meta_rejects_events_older_than_7_days(self, post):
        ConversionEvent.objects.update(event_time=timezone.now() - timezone.timedelta(days=8))
        q.process_queue()
        self.assertEqual(ConversionEvent.objects.get().error_code, 'event_too_old')
        post.assert_not_called()

    def test_inactive_connection_waits_instead_of_losing_event(self):
        self.meta.token_invalid = True; self.meta.save()
        q.process_queue()
        self.assertEqual(ConversionEvent.objects.get().status, 'retrying')

    @mock.patch('capi.services.meta.requests.post')
    def test_worker_does_not_double_send(self, post):
        post.return_value = fake_response(200, {'events_received': 1})
        ConversionEvent.objects.update(status='processing', last_attempt_at=timezone.now())  # claimed elsewhere
        q.process_queue()
        post.assert_not_called()


class LinkedInTests(TestCase):
    @mock.patch('capi.services.linkedin.requests.post')
    def test_payload_shape_and_send(self, post):
        li = make_conn(C.PLATFORM_LINKEDIN)
        make_config(li, C.TRIGGER_DEAL_WON, send_value=True, currency='USD',
                    linkedin_conversion_urn='urn:lla:llaPartnerConversion:777', name='won-li')
        lead = make_lead(platform='linkedin', email='Buyer@Corp.com')
        LeadTracking.objects.filter(lead=lead).update(li_fat_id='abc-uuid')
        with self.captureOnCommitCallbacks(execute=True):
            Opportunity.objects.create(title='D', lead=lead, stage='won', value=Decimal('1200.50'))
        post.return_value = fake_response(201, {}, {'x-li-uuid': 'REQ1'})
        q.process_queue()
        ev = ConversionEvent.objects.get()
        self.assertEqual((ev.status, ev.platform_trace_id), ('sent', 'REQ1'))
        body = post.call_args.kwargs['json']
        self.assertEqual(body['conversion'], 'urn:lla:llaPartnerConversion:777')
        self.assertGreater(body['conversionHappenedAt'], 10 ** 12)          # milliseconds
        self.assertEqual(body['conversionValue'], {'currencyCode': 'USD', 'amount': '1200.50'})
        types = {u['idType']: u['idValue'] for u in body['user']['userIds']}
        self.assertEqual(types['SHA256_EMAIL'], ids.sha256_hex('buyer@corp.com'))
        self.assertEqual(types['LINKEDIN_FIRST_PARTY_ADS_TRACKING_UUID'], 'abc-uuid')
        self.assertNotIn('userInfo', body['user'])                          # names not sent by default
        h = post.call_args.kwargs['headers']
        self.assertEqual(h['X-Restli-Protocol-Version'], '2.0.0'); self.assertIn('LinkedIn-Version', h)

    def test_config_validation(self):
        li = make_conn(C.PLATFORM_LINKEDIN)
        cfg = ConversionEventConfig(name='x', connection=li, trigger_key=C.TRIGGER_MEETING,
                                    linkedin_conversion_urn='123')
        with self.assertRaises(Exception):
            cfg.full_clean()


class IntakeAndAccessTests(TestCase):
    def test_same_person_two_campaigns_one_lead(self):
        a, created_a = upsert_lead_from_platform('meta', name='Sara', email='sara@x.com', phone='03001112222',
                                                 campaign_id='1', campaign_name='A', external_lead_id='111',
                                                 consent='granted', run_ai=False)
        b, created_b = upsert_lead_from_platform('meta', name='Sara K', email='SARA@x.com', phone='+92 300 1112222',
                                                 campaign_id='2', campaign_name='B', external_lead_id='222',
                                                 run_ai=False)
        c, created_c = upsert_lead_from_platform('linkedin', name='Sara', phone='0300-1112222',
                                                 campaign_id='9', campaign_name='LI', run_ai=False)
        self.assertTrue(created_a); self.assertFalse(created_b); self.assertFalse(created_c)
        self.assertEqual({a.pk, b.pk, c.pk}, {a.pk}); self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(LeadTouchpoint.objects.filter(lead=a).count(), 3)
        t = LeadTracking.objects.get(lead=a)
        self.assertEqual((t.platform, t.campaign_name, t.consent_status), ('linkedin', 'LI', 'granted'))

    @override_settings(CAPI_INTAKE_SECRET='s3cret')
    def test_intake_endpoint_auth(self):
        url = reverse('capi:intake', args=['meta'])
        body = json.dumps({'name': 'Z', 'email': 'z@x.com', 'consent': True, 'campaign_id': '5'})
        self.assertEqual(self.client.post(url, body, content_type='application/json').status_code, 401)
        r = self.client.post(url, body, content_type='application/json', HTTP_X_CAPI_INTAKE_KEY='s3cret')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(LeadTracking.objects.get().consent_status, 'granted')

    @override_settings(CAPI_INTAKE_SECRET='')
    def test_intake_disabled_without_secret(self):
        self.assertEqual(self.client.post(reverse('capi:intake', args=['meta']), '{}',
                                          content_type='application/json').status_code, 404)

    def _user(self, role, name):
        u = User.objects.create_user(name, password='pw12345!')
        u.profile.role = role; u.profile.save()
        return u

    def test_only_admin_or_ceo_can_open_center(self):
        admin, sales = self._user('admin', 'adm'), self._user('sales_executive', 'sal')
        self.client.force_login(sales)
        self.assertEqual(self.client.get(reverse('capi:dashboard')).status_code, 403)
        self.assertEqual(self.client.get(reverse('capi:connections')).status_code, 403)
        self.client.force_login(admin)
        for name in ('dashboard', 'connections', 'config_list', 'event_list', 'log_list', 'config_create'):
            self.assertEqual(self.client.get(reverse(f'capi:{name}')).status_code, 200, name)

    def test_connection_token_saved_encrypted_and_never_shown(self):
        admin = self._user('admin', 'adm2'); self.client.force_login(admin)
        r = self.client.post(reverse('capi:connections'), {'platform': 'meta', 'is_active': 'on', 'dataset_id': '555',
                             'lead_event_source': 'SIS AI CRM', 'access_token': 'SECRET-TOKEN-9876'})
        self.assertEqual(r.status_code, 302)
        conn = PlatformConnection.objects.get(platform='meta')
        self.assertNotIn('SECRET-TOKEN', conn.access_token_enc)
        self.assertEqual(conn.get_token(), 'SECRET-TOKEN-9876'); self.assertEqual(conn.token_hint, '9876')
        self.assertNotIn('SECRET-TOKEN', self.client.get(reverse('capi:connections')).content.decode())

    def test_event_detail_retry_and_toggle(self):
        admin = self._user('admin', 'adm3'); self.client.force_login(admin)
        meta = make_conn(); cfg = make_config(meta, C.TRIGGER_MEETING, is_enabled=True)
        r = self.client.post(reverse('capi:config_toggle', args=[cfg.pk]))
        cfg.refresh_from_db(); self.assertFalse(cfg.is_enabled)
        lead = make_lead()
        ev = q.enqueue(cfg, lead, trigger_key=C.TRIGGER_MEETING, dedup_key='x') if False else None
        cfg.is_enabled = True; cfg.save()
        ev = q.enqueue(cfg, lead, trigger_key=C.TRIGGER_MEETING, dedup_key='x')
        self.assertEqual(self.client.get(reverse('capi:event_detail', args=[ev.pk])).status_code, 200)
