"""Meta Lead Ads webhook + Conversions API end-to-end tests (Graph API is always mocked -
no real Meta calls, no fake data is ever sent anywhere)."""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest import mock

import requests
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from leads import duplicate_detector
from leads.models import Activity, Lead, Opportunity

from . import constants as C
from .models import (ConversionEvent, ConversionEventConfig, LeadTouchpoint, LeadTracking,
                     MetaWebhookEvent, PlatformConnection)
from .services import meta_lead_ads as mla
from .services import meta_webhook as mwh
from .services import queue as q

SECRET = 'appsecret'
PAGE = '1111222233334444'
LEADGEN = '555566667777888'


def sign(body: bytes, secret=SECRET):
    return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def payload(leadgen=LEADGEN, page=PAGE):
    return json.dumps({'object': 'page', 'entry': [{'id': page, 'time': 1, 'changes': [{
        'field': 'leadgen', 'value': {'leadgen_id': leadgen, 'page_id': page, 'form_id': 'F1',
                                      'ad_id': 'AD1', 'adgroup_id': 'AS1', 'created_time': 1}}]}]}).encode()


def graph_lead(leadgen=LEADGEN, email='Sara@Example.com', phone='p:+923001112222', **kw):
    data = {'id': leadgen, 'created_time': '2026-10-01T10:00:00+0000', 'form_id': 'F1',
            'ad_id': 'AD1', 'adset_id': 'AS1', 'campaign_id': 'CMP1',
            'ad_name': 'Video Ad', 'adset_name': 'Lahore 25-45', 'campaign_name': 'Spring Offer',
            'field_data': [
                {'name': 'full_name', 'values': ['Sara Khan']},
                {'name': 'email', 'values': [email]},
                {'name': 'phone_number', 'values': [phone]},
                {'name': 'company_name', 'values': ['Acme Pvt']},
                {'name': 'what_is_your_budget?', 'values': ['$5k-10k']},
                {'name': 'services_needed', 'values': ['CRM', 'Mobile app']},
            ]}
    data.update(kw)
    return data


def resp(status=200, js=None):
    r = mock.Mock(); r.status_code = status; r.json.return_value = js or {}
    r.text = json.dumps(js or {}); r.content = r.text.encode()
    return r


def make_meta_conn(**kw):
    conn, _ = PlatformConnection.objects.get_or_create(platform='meta')
    conn.is_active, conn.page_id, conn.dataset_id = True, PAGE, 'DS1'
    conn.webhook_verify_token = 'verify-me'
    conn.set_token('CAPI-TOKEN-1234'); conn.set_page_token('PAGE-TOKEN-9876')
    for k, v in kw.items():
        setattr(conn, k, v)
    conn.save()
    return conn


def post_hook(client, body, signature=None):
    return client.post(reverse('capi:meta_webhook'), body, content_type='application/json',
                       HTTP_X_HUB_SIGNATURE_256=sign(body) if signature is None else signature)


@override_settings(META_APP_SECRET=SECRET, CAPI_ACTIVITY_MODEL='leads.Activity')
class LeadAdsImportTests(TestCase):
    def setUp(self):
        self.conn = make_meta_conn()
        duplicate_detector.CALLS.clear()

    # ---- security
    def test_handshake(self):
        url = reverse('capi:meta_webhook')
        ok = self.client.get(url, {'hub.mode': 'subscribe', 'hub.verify_token': 'verify-me', 'hub.challenge': '777'})
        self.assertEqual((ok.status_code, ok.content), (200, b'777'))
        self.conn.refresh_from_db(); self.assertIsNotNone(self.conn.webhook_verified_at)
        for bad in ({'hub.mode': 'subscribe', 'hub.verify_token': 'nope', 'hub.challenge': '1'},
                    {'hub.mode': 'subscribe', 'hub.verify_token': '', 'hub.challenge': '1'},
                    {'hub.mode': 'unsubscribe', 'hub.verify_token': 'verify-me', 'hub.challenge': '1'}):
            self.assertEqual(self.client.get(url, bad).status_code, 403)

    @override_settings(META_WEBHOOK_VERIFY_TOKEN='from-env')
    def test_handshake_with_env_token(self):
        r = self.client.get(reverse('capi:meta_webhook'),
                            {'hub.mode': 'subscribe', 'hub.verify_token': 'from-env', 'hub.challenge': '9'})
        self.assertEqual(r.status_code, 200)

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_unsigned_or_badly_signed_post_is_rejected(self, get):
        body = payload()
        for sig in ('', 'sha256=deadbeef', sign(body, 'wrong-secret'), sign(body)[7:]):
            self.assertEqual(post_hook(self.client, body, sig).status_code, 401)
        self.assertEqual(self.client.post(reverse('capi:meta_webhook'), body,
                                          content_type='application/json').status_code, 401)
        self.assertEqual(MetaWebhookEvent.objects.count(), 0)
        get.assert_not_called()

    @override_settings(META_APP_SECRET='')
    def test_no_app_secret_configured_rejects_everything(self):
        self.assertEqual(post_hook(self.client, payload(), sign(payload(), '')).status_code, 401)

    # ---- the happy path
    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_lead_form_submission_creates_lead_with_everything(self, get):
        get.return_value = resp(200, graph_lead())
        with self.captureOnCommitCallbacks(execute=True):
            r = post_hook(self.client, payload())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['accepted'], 1)

        lead = Lead.objects.get()
        self.assertEqual((lead.name, lead.email, lead.lead_source), ('Sara Khan', 'sara@example.com', 'facebook'))
        self.assertEqual(lead.contact_number, '+923001112222')           # "p:" prefix stripped
        self.assertIn('Company: Acme Pvt', lead.detail)
        self.assertIn('What is your budget?: $5k-10k', lead.detail)
        self.assertIn('Services needed: CRM, Mobile app', lead.detail)

        t = LeadTracking.objects.get(lead=lead)
        self.assertEqual((t.platform, t.meta_leadgen_id, t.external_lead_id), ('meta', LEADGEN, LEADGEN))
        self.assertEqual((t.page_id, t.form_id), (PAGE, 'F1'))
        self.assertEqual((t.campaign_id, t.campaign_name), ('CMP1', 'Spring Offer'))
        self.assertEqual((t.adset_id, t.adset_name), ('AS1', 'Lahore 25-45'))
        self.assertEqual((t.ad_id, t.ad_name), ('AD1', 'Video Ad'))
        self.assertEqual(t.consent_status, 'granted')

        tp = LeadTouchpoint.objects.get()
        self.assertEqual(tp.form_answers['what_is_your_budget?'], '$5k-10k')
        self.assertEqual(tp.form_answers['services_needed'], 'CRM, Mobile app')

        act = Activity.objects.get(lead=lead)                              # CRM timeline entry
        self.assertEqual(act.title, 'Lead created from Facebook/Meta Lead Ad')
        self.assertIn('Spring Offer', act.description); self.assertIn(LEADGEN, act.description)
        self.assertIn('$5k-10k', act.description)

        ev = MetaWebhookEvent.objects.get()
        self.assertTrue(ev.processed); self.assertEqual(ev.lead, lead)
        self.assertEqual(ev.lead_data['campaign_name'], 'Spring Offer')
        # existing AI pipeline ran after commit
        self.assertEqual([c[0] for c in duplicate_detector.CALLS], ['dup', 'assign', 'score'])
        # the Graph call used the Page token, versioned URL, and an appsecret_proof
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith(f'/v25.0/{LEADGEN}'))
        self.assertEqual(kwargs['params']['access_token'], 'PAGE-TOKEN-9876')
        self.assertEqual(kwargs['params']['appsecret_proof'],
                         hmac.new(SECRET.encode(), b'PAGE-TOKEN-9876', hashlib.sha256).hexdigest())

    # ---- idempotency / dedup
    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_replayed_webhook_is_a_noop(self, get):
        get.return_value = resp(200, graph_lead())
        for _ in range(3):
            self.assertEqual(post_hook(self.client, payload()).status_code, 200)
        self.assertEqual((Lead.objects.count(), LeadTouchpoint.objects.count(),
                          Activity.objects.count(), MetaWebhookEvent.objects.count()), (1, 1, 1, 1))
        self.assertEqual(get.call_count, 1)                                # Graph fetched once

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_same_person_new_submission_updates_same_lead(self, get):
        get.return_value = resp(200, graph_lead())
        post_hook(self.client, payload())
        get.return_value = resp(200, graph_lead('999000111222333', email='SARA@example.com', phone='',
                                                campaign_id='CMP2', campaign_name='Summer'))
        post_hook(self.client, payload('999000111222333'))
        self.assertEqual(Lead.objects.count(), 1)
        t = LeadTracking.objects.get()
        self.assertEqual((t.campaign_name, t.meta_leadgen_id), ('Summer', LEADGEN))   # latest touch, first id kept
        self.assertEqual(LeadTouchpoint.objects.count(), 2)
        self.assertEqual(list(Activity.objects.values_list('title', flat=True).order_by('id')),
                         ['Lead created from Facebook/Meta Lead Ad',
                          'Existing lead re-engaged via Facebook/Meta Lead Ad'])

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_phone_only_match_and_phone_only_lead(self, get):
        Lead.objects.create(name='Old', contact_number='0300 1112222', email='')
        get.return_value = resp(200, graph_lead(email=''))
        post_hook(self.client, payload())
        self.assertEqual(Lead.objects.count(), 1)
        get.return_value = resp(200, graph_lead('42', email='', phone='+971501234567'))
        post_hook(self.client, payload('42'))                              # brand-new phone-only lead
        self.assertEqual(Lead.objects.count(), 2)

    def test_db_unique_constraints_back_the_idempotency(self):
        MetaWebhookEvent.objects.create(leadgen_id='1')
        with self.assertRaises(IntegrityError), transaction.atomic():
            MetaWebhookEvent.objects.create(leadgen_id='1')
        a, b = Lead.objects.create(name='a'), Lead.objects.create(name='b')
        LeadTracking.objects.create(lead=a, meta_leadgen_id='77')
        with self.assertRaises(IntegrityError), transaction.atomic():
            LeadTracking.objects.create(lead=b, meta_leadgen_id='77')
        LeadTracking.objects.create(lead=b, meta_leadgen_id='')            # blanks are allowed many times
        c = Lead.objects.create(name='c'); LeadTracking.objects.create(lead=c, meta_leadgen_id='')

    # ---- retry handling
    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_graph_outage_is_stored_retried_and_recovers(self, get):
        get.return_value = resp(500, {'error': {'message': 'Service unavailable', 'code': 2}})
        r = post_hook(self.client, payload())
        self.assertEqual((r.status_code, r.json()['queued']), (200, 1))   # Meta is told OK; retry is ours
        ev = MetaWebhookEvent.objects.get()
        self.assertEqual((ev.processed, ev.attempts, ev.gave_up), (False, 1, False))
        self.assertGreater(ev.next_attempt_at, timezone.now())
        self.assertIn('Service unavailable', ev.error)
        self.assertEqual(Lead.objects.count(), 0)

        self.assertEqual(mwh.retry_due()['processed'], 0)                  # back-off not elapsed
        MetaWebhookEvent.objects.update(next_attempt_at=timezone.now())
        get.return_value = resp(200, graph_lead())
        self.assertEqual(mwh.retry_due()['processed'], 1)
        ev.refresh_from_db()
        self.assertTrue(ev.processed); self.assertEqual(ev.error, ''); self.assertEqual(Lead.objects.count(), 1)

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_gives_up_then_manual_retry(self, get):
        get.return_value = resp(400, {'error': {'message': 'Invalid OAuth', 'code': 190}})
        post_hook(self.client, payload())
        for _ in range(C.WEBHOOK_MAX_ATTEMPTS):
            MetaWebhookEvent.objects.update(next_attempt_at=timezone.now())
            mwh.retry_due()
        ev = MetaWebhookEvent.objects.get()
        self.assertEqual((ev.gave_up, ev.attempts, ev.processed), (True, C.WEBHOOK_MAX_ATTEMPTS, False))
        self.assertEqual(mwh.retry_due(), {'processed': 0, 'skipped': 0, 'retry': 0, 'gave_up': 0})
        get.return_value = resp(200, graph_lead())
        self.assertEqual(mwh.reset_and_process(ev), 'processed')
        self.assertEqual(Lead.objects.count(), 1)

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_tokens_never_land_in_errors_or_logs(self, get):
        get.side_effect = requests.ConnectionError(
            'HTTPSConnectionPool: Max retries exceeded with url: /v25.0/1?access_token=PAGE-TOKEN-9876&fields=id')
        post_hook(self.client, payload())
        ev = MetaWebhookEvent.objects.get()
        self.assertNotIn('PAGE-TOKEN-9876', ev.error); self.assertIn('[redacted]', ev.error)

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_missing_page_token_is_retryable(self, get):
        self.conn.set_page_token(''); self.conn.save()
        post_hook(self.client, payload())
        ev = MetaWebhookEvent.objects.get()
        self.assertEqual((ev.attempts, ev.processed), (1, False)); self.assertIn('Page Access Token', ev.error)
        get.assert_not_called()

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_crm_failure_rolls_back_and_retries(self, get):
        get.return_value = resp(200, graph_lead())
        with mock.patch('capi.services.meta_webhook.upsert_lead_from_platform', side_effect=RuntimeError('db down')):
            post_hook(self.client, payload())
        self.assertEqual((Lead.objects.count(), MetaWebhookEvent.objects.get().attempts), (0, 1))
        MetaWebhookEvent.objects.update(next_attempt_at=timezone.now())
        self.assertEqual(mwh.retry_due()['processed'], 1)

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_extended_fields_fall_back_when_graph_rejects_them(self, get):
        bad = resp(400, {'error': {'message': 'Invalid field', 'code': 100}})
        base = graph_lead()
        for k in ('ad_name', 'adset_name', 'campaign_name'):
            base.pop(k)
        get.side_effect = [bad, resp(200, base)]
        post_hook(self.client, payload())
        self.assertEqual(get.call_count, 2)
        self.assertNotIn('ad_name', get.call_args.kwargs['params']['fields'])
        t = LeadTracking.objects.get(); self.assertEqual((t.campaign_id, t.campaign_name), ('CMP1', ''))

    def test_unknown_page_is_ignored_safely(self):
        r = post_hook(self.client, payload(page='999'))
        self.assertEqual((r.status_code, MetaWebhookEvent.objects.count()), (200, 0))
        self.assertIn('unknown_page:999', r.json()['errors'])

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_timeline_or_hook_problems_never_lose_the_lead(self, get):
        get.return_value = resp(200, graph_lead())
        with override_settings(CAPI_ACTIVITY_MODEL='nope.Missing',
                               CAPI_POST_IMPORT_HOOKS=['leads.hooks.does_not_exist'],
                               CAPI_CAMPAIGN_RESOLVER='leads.hooks.nope'):
            with self.captureOnCommitCallbacks(execute=True):
                post_hook(self.client, payload())
        self.assertEqual((Lead.objects.count(), Activity.objects.count()), (1, 0))
        self.assertTrue(MetaWebhookEvent.objects.get().processed)

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_activity_model_is_autodetected(self, get):
        get.return_value = resp(200, graph_lead())
        with override_settings(CAPI_ACTIVITY_MODEL=''):
            post_hook(self.client, payload())
        self.assertEqual(Activity.objects.count(), 1)

    @override_settings(CAPI_POST_IMPORT_HOOKS=['leads.hooks.followup'], CAPI_CAMPAIGN_RESOLVER='leads.hooks.campaign')
    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_followup_and_campaign_hooks_run(self, get):
        from leads import hooks
        hooks.SEEN.clear()
        get.return_value = resp(200, graph_lead())
        with self.captureOnCommitCallbacks(execute=True):
            post_hook(self.client, payload())
        lead = Lead.objects.get()
        self.assertEqual(hooks.SEEN, [('campaign', 'CMP1'), (lead.pk, True)])


@override_settings(META_APP_SECRET=SECRET, CAPI_ACTIVITY_MODEL='leads.Activity')
class ConversionsBackToMetaTests(TestCase):
    """Imported Facebook lead -> CRM progresses -> CAPI sends real downstream events."""

    def setUp(self):
        self.conn = make_meta_conn()
        for name, trig, to, ev in (('Qualified', C.TRIGGER_OPP_STAGE, 'qualified', 'QualifiedLead'),
                                   ('Proposal', C.TRIGGER_OPP_STAGE, 'proposal', 'ProposalSent'),
                                   ('Won', C.TRIGGER_DEAL_WON, '', 'Purchase')):
            ConversionEventConfig.objects.create(
                name=name, connection=self.conn, trigger_key=trig, to_value=to, platform_event_name=ev,
                is_enabled=True, send_value=trig == C.TRIGGER_DEAL_WON, currency='USD')

    @mock.patch('capi.services.meta.requests.post')
    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_full_lifecycle(self, get, post):
        get.return_value = resp(200, graph_lead())
        with self.captureOnCommitCallbacks(execute=True):
            post_hook(self.client, payload())
        lead = Lead.objects.get()
        opp = Opportunity.objects.create(title='CRM build', lead=lead, stage='new', value=Decimal('4000'))
        for stage in ('qualified', 'proposal', 'won', 'proposal', 'won'):      # each save = own transaction
            with self.captureOnCommitCallbacks(execute=True):
                opp.stage = stage; opp.save()                                  # flapping must not duplicate
        self.assertEqual(sorted(ConversionEvent.objects.values_list('event_name', flat=True)),
                         ['ProposalSent', 'Purchase', 'QualifiedLead'])
        post.return_value = resp(200, {'events_received': 1, 'fbtrace_id': 'T'})
        self.assertEqual(q.process_queue()['sent'], 3)
        sent = [c.kwargs['json']['data'][0] for c in post.call_args_list]
        won = next(e for e in sent if e['event_name'] == 'Purchase')
        self.assertEqual(won['user_data']['lead_id'], int(LEADGEN))            # Meta Conversion Leads match key
        self.assertEqual(won['custom_data']['lead_event_source'], 'SIS AI CRM')
        self.assertEqual(won['custom_data']['crm_campaign_name'], 'Spring Offer')
        self.assertEqual(won['custom_data']['value'], 4000.0)
        self.assertEqual(len({e['event_id'] for e in sent}), 3)
        self.assertEqual(q.process_queue(), {'sent': 0, 'retrying': 0, 'failed': 0, 'skipped': 0})  # nothing re-sent

    @mock.patch('capi.services.meta.requests.post')
    def test_no_event_for_leads_that_did_not_come_from_meta(self, post):
        manual = Lead.objects.create(name='Walk-in', email='w@x.com')            # no tracking -> never sent
        with self.captureOnCommitCallbacks(execute=True):
            Opportunity.objects.create(title='x', lead=manual, stage='qualified')
        self.assertEqual(ConversionEvent.objects.count(), 0)
        post.assert_not_called()


@override_settings(META_APP_SECRET=SECRET, CAPI_ACTIVITY_MODEL='leads.Activity')
class AdminScreenTests(TestCase):
    def setUp(self):
        self.conn = make_meta_conn()
        self.admin = User.objects.create_user('adm', password='x')
        self.admin.profile.role = 'admin'; self.admin.profile.save()
        sales = User.objects.create_user('sal', password='x')
        self.client.force_login(self.admin); self.sales = sales

    def test_status_panel_and_pages_render(self):
        MetaWebhookEvent.objects.create(leadgen_id='1', error='boom', page_id=PAGE)
        r = self.client.get(reverse('capi:connections'))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('/capi/webhook/meta/', body); self.assertIn('Meta App Secret', body)
        self.assertNotIn('PAGE-TOKEN-9876', body); self.assertNotIn('CAPI-TOKEN-1234', body)
        self.assertEqual(self.client.get(reverse('capi:meta_event_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('capi:meta_event_list'), {'state': 'retrying', 'q': '1'}).status_code, 200)

    def test_only_admins(self):
        self.client.force_login(self.sales)
        for name in ('meta_event_list', 'connections'):
            self.assertEqual(self.client.get(reverse(f'capi:{name}')).status_code, 403)
        for name in ('meta_subscribe_page', 'meta_page_test', 'meta_retry_all'):
            self.assertEqual(self.client.post(reverse(f'capi:{name}')).status_code, 403)

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    @mock.patch('capi.services.meta_lead_ads.requests.post')
    def test_subscribe_and_page_test(self, post, get):
        post.return_value = resp(200, {'success': True})
        get.return_value = resp(200, {'data': [{'id': 'APP', 'subscribed_fields': ['leadgen']}]})
        self.client.post(reverse('capi:meta_subscribe_page'))
        self.conn.refresh_from_db(); self.assertIsNotNone(self.conn.webhook_subscribed_at)
        self.assertEqual(post.call_args.kwargs['data']['subscribed_fields'], 'leadgen')
        r = self.client.post(reverse('capi:meta_page_test'), follow=True)
        self.assertEqual(r.status_code, 200)

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    @mock.patch('capi.services.meta_lead_ads.requests.post')
    def test_subscribe_not_confirmed_is_reported(self, post, get):
        post.return_value = resp(200, {'success': True})
        get.return_value = resp(200, {'data': []})
        self.client.post(reverse('capi:meta_subscribe_page'))
        self.conn.refresh_from_db(); self.assertIsNone(self.conn.webhook_subscribed_at)

    @mock.patch('capi.services.meta_lead_ads.requests.get')
    def test_manual_retry_button(self, get):
        ev = MetaWebhookEvent.objects.create(leadgen_id=LEADGEN, page_id=PAGE, gave_up=True, attempts=6, error='x')
        get.return_value = resp(200, graph_lead())
        self.client.post(reverse('capi:meta_event_retry', args=[ev.pk]))
        ev.refresh_from_db(); self.assertTrue(ev.processed); self.assertEqual(Lead.objects.count(), 1)

    def test_verify_token_autogenerated_on_save(self):
        PlatformConnection.objects.filter(platform='meta').update(webhook_verify_token='')
        self.client.post(reverse('capi:connections'), {'platform': 'meta', 'is_active': 'on', 'dataset_id': 'DS1',
                                                       'page_id': PAGE, 'lead_event_source': 'SIS AI CRM'})
        self.assertGreaterEqual(len(PlatformConnection.objects.get(platform='meta').webhook_verify_token), 24)

    def test_intake_endpoint_still_accepts_new_fields(self):
        with override_settings(CAPI_INTAKE_SECRET='s'):
            r = self.client.post(reverse('capi:intake', args=['meta']), json.dumps({
                'email': 'a@b.com', 'consent': True, 'page_id': 'P', 'ad_name': 'AdX',
                'form_answers': {'q': 'a'}, 'external_lead_id': '31'}),
                content_type='application/json', HTTP_X_CAPI_INTAKE_KEY='s')
        self.assertEqual(r.status_code, 201)
        t = LeadTouchpoint.objects.get(); self.assertEqual((t.page_id, t.ad_name, t.form_answers), ('P', 'AdX', {'q': 'a'}))


class ConcurrentIntakeTests(TransactionTestCase):
    """Real threads on a real PostgreSQL connection (skipped on SQLite)."""

    def test_two_simultaneous_submissions_make_one_lead(self):
        import threading
        from django.db import connection, connections
        if connection.vendor != 'postgresql':
            self.skipTest('needs PostgreSQL')
        from .services.intake import upsert_lead_from_platform
        results, barrier = [], threading.Barrier(2)

        def run(ext):
            try:
                barrier.wait()
                results.append(upsert_lead_from_platform(
                    'meta', name='Racer', email='race@x.com', phone='03005550000', external_lead_id=ext, run_ai=False))
            finally:
                connections.close_all()
        ts = [threading.Thread(target=run, args=(str(i),)) for i in (101, 102)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(sorted(c for _, c in results), [False, True])
        self.assertEqual(LeadTouchpoint.objects.count(), 2)
