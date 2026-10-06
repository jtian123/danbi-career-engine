"""Career Hub database: her actions persist, history is kept, learning is bounded."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

if not os.environ.get('DANBI_DATA'):
    os.environ['DANBI_DATA'] = str(Path(__file__).resolve().parents[1] / 'examples' / 'data')

from src.hub import db  # noqa: E402


def job(key, **kw):
    base = dict(job_key=key, company='Target', title='Marketing Intern', url='https://e.com/' + key, lane='commerce',
                industry='retail', tier='enterprise', reviewed=True, fit=70, bucket='apply', review={'why': 'x'})
    base.update(kw)
    return base


class HubTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get('DANBI_DATA')
        os.environ['DANBI_DATA'] = self._tmp.name

    def tearDown(self):
        os.environ['DANBI_DATA'] = self._old or ''
        self._tmp.cleanup()

    def test_status_and_rating_survive_resurfacing(self):
        db.upsert_surfaced([job('a')], '2026-10-06', 's1')
        jid = db.full_state()['jobs'][0]['id']
        db.set_status(jid, 'applied')
        db.rate(jid, 'interested', '', 'learn', 'liked the team')
        db.upsert_surfaced([job('a', title='Retitled', fit=10)], '2026-10-07', 's2')
        j = db.full_state()['jobs'][0]
        self.assertEqual((j['status'], j['interest'], j['notes'], j['title']), ('applied', 'interested', 'liked the team', 'Marketing Intern'))
        kinds = [e['kind'] for e in db.full_state()['events']]
        self.assertIn('applied', kinds)
        self.assertIn('rated', kinds)

    def test_unreviewed_listing_never_replaces_a_review(self):
        db.upsert_surfaced([job('a')], '2026-10-06', 's1')
        db.upsert_surfaced([job('a', reviewed=False, fit=None, review=None, prescore=40)], '2026-10-07', 's2')
        j = db.full_state()['jobs'][0]
        self.assertEqual((j['reviewed'], j['fit']), (1, 70))

    def test_refound_listing_never_moves_a_job_to_a_new_day(self):
        db.upsert_surfaced([job('a')], '2026-09-16', 'old')
        db.upsert_surfaced([job('a', reviewed=False, fit=None, review=None, prescore=50)], '2026-10-06', 'new')
        self.assertEqual(db.full_state()['jobs'][0]['last_surfaced'], '2026-09-16')
        db.upsert_surfaced([job('a', fit=75)], '2026-10-07', 'review')       # a fresh review is news that day
        self.assertEqual(db.full_state()['jobs'][0]['last_surfaced'], '2026-10-07')

    def test_preferences_bounded_and_reason_aware(self):
        rows = [job(f'k{i}', lane='commerce', industry='beauty') for i in range(40)]
        rows += [job('pay', lane='finance', industry='finance'), job('ind', lane='data', industry='insurance')]
        db.upsert_surfaced(rows, '2026-10-06', 's1')
        ids = {j['job_key']: j['id'] for j in db.full_state()['jobs']}
        for i in range(40):
            db.rate(ids[f'k{i}'], 'interested')
        db.rate(ids['pay'], 'not_for_me', 'pay')
        db.rate(ids['ind'], 'not_for_me', 'industry')
        p = db.preferences()
        self.assertLessEqual(p['lane']['commerce']['adjust'], 10)
        self.assertNotIn('finance', p['lane'])            # a pay dislike says nothing about the career
        self.assertNotIn('data', p['lane'])               # an industry dislike is not a direction dislike
        self.assertLess(p['industry']['insurance']['adjust'], 0)
        with self.assertRaises(ValueError):
            db.rate(ids['pay'], 'love it')

    def test_suppression_and_leads(self):
        db.upsert_surfaced([job('a'), job('b')], '2026-10-06', 's1')
        ids = {j['job_key']: j['id'] for j in db.full_state()['jobs']}
        db.set_status(ids['a'], 'dismissed')
        self.assertEqual(db.suppressed_keys(), {'a'})
        lead = db.add_lead('Hyundai Motor America', 'Marketing Intern', 'https://e.com/h', 'career fair', deadline='2026-11-01')
        self.assertEqual((lead['status'], lead['origin']), ('saved', 'lead'))
        with self.assertRaises(ValueError):
            db.add_lead('X', 'Y', 'javascript:alert(1)')

    def test_server_round_trip(self):
        from http.server import ThreadingHTTPServer
        from src.hub.server import Handler
        db.upsert_surfaced([job('a')], '2026-10-06', 's1')
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            base = f'http://127.0.0.1:{httpd.server_address[1]}'
            st = json.load(urllib.request.urlopen(base + '/api/state'))
            jid = st['jobs'][0]['id']
            req = urllib.request.Request(base + '/api/status', data=json.dumps({'id': jid, 'status': 'saved'}).encode(),
                                         headers={'Content-Type': 'application/json'})
            self.assertEqual(json.load(urllib.request.urlopen(req))['job']['status'], 'saved')
            page = urllib.request.urlopen(base + '/').read().decode()
            self.assertIn('Career Hub', page)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + '/jd/../../data/profile.json')
        finally:
            httpd.shutdown()


if __name__ == '__main__':
    unittest.main()
