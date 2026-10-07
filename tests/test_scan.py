"""Scanner: filters, adapters, merging, queue, and the mark → hub round trip."""
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

if not os.environ.get('DANBI_DATA'):
    os.environ['DANBI_DATA'] = str(Path(__file__).resolve().parents[1] / 'examples' / 'data')

from src.scan import classify as C, net, run, sources, state  # noqa: E402

DAY = date(2026, 10, 6)


def row(**kw):
    base = dict(source='ats', board='greenhouse:x', company='Target', title='Inventory Analyst Intern',
                location='Minneapolis, MN', url='https://example.com/jobs/1', posted='2026-10-01',
                date_precision='absolute', req_id='1', description='', pay_text='', degrees=None, terms=None,
                detail=None, deadline=None, intern_hint=False, origin_kind='enterprise')
    base.update(kw)
    return base


class TempData:
    """Point data/ at a temporary folder so tests never touch real records."""
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get('DANBI_DATA')
        os.environ['DANBI_DATA'] = self._tmp.name
        fixture = Path(__file__).resolve().parents[1] / 'examples' / 'data' / 'profile.json'
        Path(self._tmp.name, 'profile.json').write_text(fixture.read_text())

    def tearDown(self):
        os.environ['DANBI_DATA'] = self._old or ''
        self._tmp.cleanup()


class ClassifyTests(unittest.TestCase):
    def test_html_is_decoded_and_stripped(self):
        self.assertEqual(C.text('&lt;p&gt;Analyze &amp;amp; explain&lt;/p&gt;'), 'Analyze & explain')

    def test_us_detection(self):
        for loc in ('Los Angeles, CA', 'TX-Dallas', 'Remote in USA', 'New London, CT', 'Toronto, ON; New York, NY'):
            self.assertEqual(C.us_status(loc), 'US', loc)
        for loc in ('Hamburg, Hamburg, DEU', 'London, UK', 'CAN, ON, Mississauga', 'Casablanca, Morocco', 'Breda, NB, nl'):
            self.assertEqual(C.us_status(loc), 'foreign', loc)
        for loc in ('Remote', '4 Locations', ''):
            self.assertEqual(C.us_status(loc), 'unknown', loc)

    def test_scope_keeps_business_and_ise_roles_drops_engineering(self):
        for t in ('Industrial Engineering Intern', 'Data Scientist Intern', 'Oncology Marketing Intern',
                  'Engineering Program Manager Intern', 'Sales Engineer Intern'):
            self.assertIsNone(C.out_of_scope(t), t)
        for t in ('Software Engineer Intern', 'Verification Engineer Intern', 'Machine Learning Engineer Intern',
                  'Research Scientist Intern, PhD', 'Discovery Scientist Intern', 'Stage - Hiver 2027'):
            self.assertIsNotNone(C.out_of_scope(t), t)

    def test_lanes_cover_her_directions_and_keep_the_rest(self):
        cases = {'Marketing Analytics Intern': 'commerce', 'Consumer Insights Intern': 'insights',
                 'Associate Product Manager Intern': 'product', 'Supply Chain Intern': 'operations',
                 'Sales Operations Intern': 'business', 'Pricing Analyst Intern': 'demand',
                 'FP&A Intern': 'finance', 'Strategy & Operations Intern': 'strategy',
                 'Data Analyst Intern': 'data', 'Store Executive Intern': 'explore'}
        for title, lane in cases.items():
            self.assertEqual(C.lane_of(title)[0], lane, title)
        self.assertTrue(C.lane_of('Data Science Intern')[1])          # stretch

    def test_eligibility_flags(self):
        e = C.eligibility('Business Analyst Intern', "Open to bachelor's or master's students. Must be a U.S. citizen.")
        self.assertEqual(e['degree_rule'], 'graduate_allowed')
        self.assertTrue(e['citizenship_only'])
        self.assertEqual(C.eligibility('Procurement Internship (Undergraduate)', 'graduate degree')['degree_rule'],
                         'undergraduate_only')
        # ITAR "U.S. person" is NOT a citizenship requirement: permanent residents qualify
        self.assertNotIn('citizenship_only', C.eligibility('Ops Intern', 'Applicants must be a U.S. person under ITAR.'))
        g = C.eligibility('Intern', 'Students graduating between December 2027 and June 2028. Minimum GPA of 3.0.')
        self.assertEqual((g['grad_min'], g['grad_max'], g['min_gpa']), ('2027-12', '2028-06', 3.0))
        self.assertIn('korean', C.eligibility('Marketing Intern', 'Fluency in Korean preferred').get('arenas', []))

    def test_seasons(self):
        self.assertEqual(C.seasons('Inventory Analyst Intern (Starting Summer, 2027)'), ['Summer 2027'])
        self.assertTrue(C.season_past('Summer 2026', DAY))
        self.assertFalse(C.season_past('Spring 2027', DAY))

    def test_core_title_collapses_city_copies(self):
        a = state.core_title('Store Executive Intern (Store Leadership Intern) - Miami- Starting Summer 2027)')
        b = state.core_title('Store Executive Intern (Store Leadership Intern) Des Moines, IA (Starting Summer 2027)')
        c = state.core_title('Store Executive Intern – North/West of Sacramento, CA (Starting Summer 2027)')
        self.assertEqual({a, b, c}, {'store executive intern'})
        self.assertNotEqual(state.core_title('Marketing Intern'), state.core_title('Finance Intern'))


class SourceTests(unittest.TestCase):
    def test_greenhouse_uses_first_published_and_company_name(self):
        raw = {'jobs': [{'id': 7, 'title': 'Marketing Intern', 'location': {'name': 'New York, NY'},
                         'absolute_url': 'https://job-boards.greenhouse.io/x/jobs/7', 'company_name': 'X Corp',
                         'first_published': '2026-10-01T10:00:00-04:00', 'updated_at': '2026-10-05T10:00:00-04:00'}]}
        with patch.object(net, 'get', return_value=raw):
            rows, inv = sources.greenhouse('', 'x')
        self.assertEqual((rows[0]['company'], rows[0]['posted'], inv), ('X Corp', '2026-10-01', 1))

    def test_block_is_raised_not_retried(self):
        calls = []

        def blocked(*a, **k):
            calls.append(1)
            raise net.Blocked('HTTP 429')
        with patch.object(net, 'get', side_effect=blocked):
            with self.assertRaises(net.Blocked):
                sources.greenhouse('', 'x')
        self.assertEqual(len(calls), 1)

    def test_workday_prefers_intern_facet_and_paginates(self):
        facets = {'total': 900, 'jobPostings': [], 'facets': [{'facetParameter': 'workerSubType', 'values': [
            {'id': 'INT', 'descriptor': 'Intern (Fixed Term)'}, {'id': 'PH', 'descriptor': 'Pharmacy Intern'}]}]}
        page = {'total': 25, 'jobPostings': [{'title': f'Finance Intern {i}', 'externalPath': f'/job/{i}',
                                              'postedOn': 'Posted 3 Days Ago', 'bulletFields': [str(i)]}
                                             for i in range(20)]}
        page2 = {'total': 0, 'jobPostings': [{'title': f'Finance Intern {i}', 'externalPath': f'/job/{i}'}
                                             for i in range(20, 25)]}
        seen = []

        def fake(url, data=None, **k):
            seen.append(data)
            if data['limit'] == 1:
                return facets
            return page if data['offset'] == 0 else page2
        with patch.object(net, 'get', side_effect=fake):
            rows, total = sources.workday('A', 'a', 'External', wd=5)
        self.assertEqual(len(rows), 25)
        self.assertEqual(seen[1]['appliedFacets'], {'workerSubType': ['INT']})   # pharmacy facet skipped

    def test_tiktok_keeps_place_chain_and_full_text(self):
        page = {'data': {'count': 1, 'job_post_list': [{'id': '1', 'code': 'A1', 'title': 'Campaign Marketing Intern',
                 'description': 'About', 'requirement': 'Minimum Qualifications', 'recruit_type': {'en_name': 'Intern'},
                 'city_info': {'en_name': 'Los Angeles', 'parent': {'en_name': 'California',
                               'parent': {'en_name': 'United States of America'}}}}]}}
        with patch.object(net, 'get', return_value=page), patch.object(sources.time, 'sleep'):
            rows, total = sources.tiktok('TikTok', 'tiktok')
        self.assertEqual(rows[0]['location'], 'Los Angeles, California, USA')
        self.assertEqual(C.us_status(rows[0]['location']), 'US')
        self.assertIn('Minimum Qualifications', rows[0]['description'])
        self.assertTrue(rows[0]['url'].startswith('https://lifeattiktok.com/search/'))

    def test_detail_links_parse(self):
        self.assertEqual(sources._detail_from_url('https://job-boards.greenhouse.io/acme/jobs/123')['kind'], 'greenhouse')
        d = sources._detail_from_url('https://target.wd5.myworkdayjobs.com/en-US/targetcareers/job/MN/Intern_R1')
        self.assertTrue(d['api'].startswith('https://target.wd5.myworkdayjobs.com/wday/cxs/target/targetcareers/job/'))


class StaffRoleTests(unittest.TestCase):
    """University staff jobs: full-time salaried roles, found on shared platforms, same pipeline."""

    def test_university_staff_roles_kept_and_tagged(self):
        rows = [row(company='University of Southern California', title='Data Analyst', url='https://e.com/1'),
                row(company='UCLA', title='Assistant Director of Admissions', url='https://e.com/2'),
                row(company='Juilliard', title='Marketing Specialist', url='https://e.com/3', source='higheredjobs')]
        kept, dropped = run.classify_rows(rows, DAY, citizen=False)
        self.assertEqual(len(kept), 3)
        self.assertEqual({(r['track'], r['industry'], r['tier']) for r in kept}, {('staff', 'higher_ed', 'university')})

    def test_not_staff_level_or_not_a_university_is_dropped(self):
        titles = ['Marketing Specialist (Part-Time)', 'Student Worker - Library', 'Adjunct Lecturer',
                  'Director of Marketing', 'Temporary Events Assistant']
        rows = [row(company='New York University', title=t, url=f'https://e.com/{i}') for i, t in enumerate(titles)]
        rows.append(row(company='Acme Corp', title='Data Analyst', url='https://e.com/x', staff_hint=True))
        kept, dropped = run.classify_rows(rows, DAY, citizen=False)
        self.assertEqual(kept, [])
        self.assertEqual(sum(dropped.values()), len(rows))

    def test_internships_at_universities_stay_internships(self):
        kept, _ = run.classify_rows([row(company='UCLA', title='Marketing Intern', url='https://e.com/i')], DAY, citizen=False)
        self.assertEqual(kept[0]['track'], 'internship')

    def test_queue_gives_staff_at_most_a_quarter(self):
        staff = [dict(row(company=f'U{i} University', title='Analyst', url=f'https://s.com/{i}'), track='staff',
                      lane='data', industry='higher_ed', prescore=99, flags={'degree_rule': 'unknown'}) for i in range(20)]
        interns = [dict(row(company=f'Co{i}', title='Marketing Intern', url=f'https://i.com/{i}'), track='internship',
                        lane='commerce', industry='retail', prescore=50, flags={'degree_rule': 'graduate_allowed'})
                   for i in range(40)]
        q = run.select_queue(staff + interns, 40)
        self.assertEqual(sum(r['track'] == 'staff' for r in q), 10)
        self.assertEqual(len(q), 40)

    def test_higheredjobs_feed_parse(self):
        xml = ('<rss><channel><item><title>Data Analyst</title><description>Pepperdine University (Malibu, CA)'
               '</description><link>https://www.higheredjobs.com/details.cfm?JobCode=1</link>'
               '<pubDate>Tue, 06 Oct 2026 08:04:21 EDT</pubDate></item></channel></rss>')
        with patch.object(net, 'get', return_value=xml), patch.object(sources.time, 'sleep'):
            rows, n = sources.higheredjobs(['31'])
        r = rows[0]
        self.assertEqual((r['company'], r['location'], r['posted'], r['staff_hint']),
                         ('Pepperdine University', 'Malibu, CA', '2026-10-06', True))

    def test_linkedin_staff_queries_use_full_time_filter(self):
        urls = []

        def fake(url, **k):
            urls.append(url)
            return ''
        with patch.object(net, 'get', side_effect=fake), patch.object(sources.time, 'sleep'):
            sources.linkedin(['marketing intern'], staff_queries=['university marketing'])
        self.assertIn('f_E=1', urls[0])
        self.assertIn('f_JT=F', urls[1])


class PipelineTests(TempData, unittest.TestCase):
    def test_filters_count_every_drop(self):
        rows = [row(), row(title='Software Engineer Intern', url='https://e.com/2'),
                row(location='London, UK', url='https://e.com/3'),
                row(title='Marketing Intern Summer 2026', url='https://e.com/4'),
                row(title='Analyst', url='https://e.com/5'),
                row(description='Must be a U.S. citizen.', title='Finance Intern', url='https://e.com/6'),
                row(company='Robert Half', title='Marketing Intern', url='https://e.com/7')]
        kept, dropped = run.classify_rows(rows, DAY, citizen=False)
        self.assertEqual([r['title'] for r in kept], ['Inventory Analyst Intern'])
        self.assertEqual(sum(dropped.values()), 6)

    def test_merge_prefers_employer_posting_over_linkedin(self):
        li = row(source='linkedin', url='https://www.linkedin.com/jobs/view/1', intern_hint=True, location='Minneapolis, MN')
        ats = row(url='https://target.wd5.myworkdayjobs.com/en-US/targetcareers/job/1', location='Brooklyn Park, MN')
        kept, _ = run.classify_rows([li, ats], DAY)
        merged = run.merge(kept)
        self.assertEqual(len(merged), 1)
        self.assertIn('myworkdayjobs', merged[0]['url'])
        self.assertEqual(merged[0]['found_by'], ['ats', 'linkedin'])

    def test_queue_caps_each_employer_and_keeps_exploration(self):
        rows = [dict(row(company='Big', title=f'Marketing Intern {i}', url=f'https://e.com/{i}'), lane='commerce',
                     industry='retail', prescore=90 - i, flags={'degree_rule': 'graduate_allowed'}) for i in range(6)]
        rows += [dict(row(company=f'Co{i}', title='Supply Chain Intern', url=f'https://f.com/{i}'), lane='operations',
                      industry='industrial', prescore=50, flags={'degree_rule': 'unknown'}) for i in range(3)]
        q = run.select_queue(rows, 5)
        self.assertLessEqual(sum(r['company'] == 'Big' for r in q), 2)
        self.assertTrue(any(r['lane'] == 'operations' for r in q))

    def test_mark_validates_and_fills_the_hub(self):
        from src.hub import db
        scan_dir = Path(self._tmp.name) / 'scan-2026-10-06'
        scan_dir.mkdir()
        q = {'job_key': 'target|inventory analyst intern', 'company': 'Target', 'title': 'Inventory Analyst Intern',
             'url': 'https://target.wd5.myworkdayjobs.com/en-US/targetcareers/job/1', 'lane': 'operations',
             'industry': 'retail', 'tier': 'enterprise', 'source': 'ats', 'prescore': 80,
             'flags': {'degree_rule': 'unknown'}, 'country': 'US'}
        rest = [dict(q, job_key='target|associate buyer intern', title='Associate Buyer Intern', lane='demand',
                     url='https://target.wd5.myworkdayjobs.com/en-US/targetcareers/job/2')]
        (scan_dir / 'queue.json').write_text(json.dumps([q]))
        (scan_dir / 'rest.json').write_text(json.dumps(rest))
        (scan_dir / 'summary.json').write_text(json.dumps({'scan_id': 'scan-2026-10-06', 'raw_rows': 1,
                                                           'internships_in_scope': 1, 'unique_jobs': 2, 'new_to_her': 2,
                                                           'queued_for_review': 1}))
        review = {'key': q['job_key'], 'company': 'Target', 'title': 'Inventory Analyst Intern', 'url': q['url'],
                  'lane': 'operations', 'industry': 'retail', 'country': 'US', 'pay': '$30/hour',
                  'timing': 'Summer 2027', 'why': 'Marketplace inventory work.', 'gaps': 'SQL in progress.',
                  'task': 'Forecast and place orders.', 'conversion': 'Not stated', 'next_step': 'Apply.',
                  'evidence_note': 'Read today.', 'verification': 'ats_live', 'verified_at': date.today().isoformat(),
                  'technical_level': 3, 'degree_rule': 'graduate_allowed', 'checks': [], 'blockers': [],
                  'decision': 'recommend', 'reviewed': True,
                  'score_components': {'transfer': 20, 'learning': 22, 'readiness': 15, 'pay': 8, 'conversion': 0, 'timing': 5}}
        bad = dict(review, why='TODO')
        (scan_dir / 'reviewed.json').write_text(json.dumps([bad]))
        with self.assertRaises(ValueError):
            run.mark(scan_dir, log=lambda m: None)
        (scan_dir / 'reviewed.json').write_text(json.dumps([review]))
        run.mark(scan_dir, log=lambda m: None)
        run.mark(scan_dir, log=lambda m: None)                       # idempotent
        st = db.full_state()
        self.assertEqual(len(st['jobs']), 2)
        rec = next(j for j in st['jobs'] if j['reviewed'])
        self.assertEqual((rec['fit'], rec['tier_bonus']), (70, 8))
        self.assertEqual(rec['priority'], 78)
        self.assertIn('target|inventory analyst intern', state.seen()['keys'])
        self.assertNotIn('target|associate buyer intern', state.seen()['keys'])   # unreviewed may be queued later


if __name__ == '__main__':
    unittest.main()
