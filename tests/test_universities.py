"""University jobs track: pay parsing, role filters, ranking, hub storage."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

if not os.environ.get('DANBI_DATA'):
    os.environ['DANBI_DATA'] = str(Path(__file__).resolve().parents[1] / 'examples' / 'data')

from src.scan import universities as U  # noqa: E402

PREFS = {'lane': {}, 'industry': {}}


def row(title, desc='', url='https://usc.wd5.myworkdayjobs.com/en-US/USCCareers/job/x', location='Los Angeles, CA'):
    return {'title': title, 'description': desc, 'url': url, 'location': location, 'company': 'USC',
            'source': 'university', 'detail': None}


class PayTests(unittest.TestCase):
    def test_annual_hourly_and_noise(self):
        p = U.pay_of('The annual base salary range for this position is $72,800.00 - $97,400.00.')
        self.assertEqual((p['min'], p['max'], p['unit']), (72800, 97400, 'year'))
        h = U.pay_of('Hourly rate: $22.00 to $25.00 per hour.')
        self.assertEqual((h['unit'], h['annual_max']), ('hour', 52000))
        self.assertIsNone(U.pay_of('USC has a $9 billion endowment and a $1,200 conference budget.'))
        self.assertEqual(U.pay_of('Salary: $85k - $95k per year')['annual_max'], 95000)
        self.assertEqual(U.pay_of('Salary range: $39,300.00 - $292,125.00')['annual_mid'], 165712)
        self.assertIsNone(U.pay_of('Pay rate: $160 for the project'))
        self.assertIsNone(U.pay_of('Oversees capital projects exceeding $500,000 and reports on spend.'))
        self.assertIsNone(U.pay_of('The College Development team raises over $100M annually.'))
        self.assertEqual(U.pay_of('Manages a $2,000,000 budget. Salary range: $70,000 - $80,000 per year.')['annual_mid'], 75000)


class FilterTests(unittest.TestCase):
    def test_keeps_her_functions_and_drops_the_rest(self):
        keep = ['Data Analyst', 'Marketing Coordinator', 'Assistant Director of Admissions', 'Student Worker - Career Center',
                'Program Coordinator, International Services', 'Institutional Research Analyst', 'Student Affairs Manager']
        drop = ['Assistant Professor of Economics', 'Clinical Research Coordinator', 'Registered Nurse',
                'Custodian', 'Software Engineer', 'Executive Director, Advancement', 'Dean of Students',
                'RN Navigator Coordinator', 'Transplant Coordinator II', 'Client Technologies Administrator']
        kept, dropped = U.classify([row(t, url=f'https://x.test/{i}') for i, t in enumerate(keep + drop)], home='USC')
        self.assertEqual(sorted(r['title'] for r in kept), sorted(keep))
        self.assertEqual(sum(dropped.values()), len(drop))
        student = next(r for r in kept if r['title'].startswith('Student Worker'))
        self.assertEqual((student['employment'], student['industry'], student['tier']), ('student', 'education', 'university'))
        manager = next(r for r in kept if r['title'] == 'Student Affairs Manager')
        self.assertEqual(manager['employment'], 'staff')          # serves students; not a student job

    def test_student_jobs_only_at_her_school(self):
        rows = [row('Student Worker - Library', url='https://x.test/1'),
                dict(row('Graduate Assistant - English', url='https://x.test/2'), company='Cal State LA')]
        kept, dropped = U.classify(rows, home='USC')
        self.assertEqual([r['company'] for r in kept], ['USC'])
        self.assertEqual(sum(dropped.values()), 1)

    def test_function_labels(self):
        self.assertEqual(U.function_of('Senior Financial Analyst'), 'Data & analytics')
        self.assertEqual(U.function_of('Admissions Counselor'), 'Admissions & enrollment')
        self.assertEqual(U.function_of('Grants Administrator'), 'Research administration')
        self.assertIsNone(U.function_of('Head Chef'))


class RankTests(unittest.TestCase):
    def test_pay_drives_rank_and_good_line(self):
        kept, _ = U.classify([row('Marketing Coordinator', url='https://x.test/a'), row('Data Analyst', url='https://x.test/b')], home='USC')
        a, b = kept
        a['pay'] = U.pay_of('Salary range $55,000 - $60,000 per year')
        b['pay'] = U.pay_of('Salary range $80,000 - $95,000 per year')
        sa, _, good_a = U.score(a, PREFS)
        sb, _, good_b = U.score(b, PREFS)
        self.assertGreater(sb, sa)
        self.assertEqual((good_a, good_b), (False, True))


class HubStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get('DANBI_DATA')
        os.environ['DANBI_DATA'] = self._tmp.name

    def tearDown(self):
        os.environ['DANBI_DATA'] = self._old or ''
        self._tmp.cleanup()

    def test_old_database_gains_university_columns(self):
        hub = Path(self._tmp.name, 'hub')
        hub.mkdir()
        from src.hub import db
        con = sqlite3.connect(str(hub / 'hub.db'))
        con.executescript(db._SCHEMA)          # the first-release schema, without the new columns
        con.execute("INSERT INTO jobs(job_key, company) VALUES('a','Old')")
        con.commit(); con.close()
        c = db.connect()
        cols = {r[1] for r in c.execute('PRAGMA table_info(jobs)')}
        self.assertTrue({'track', 'pay_annual_max', 'employment', 'university'} <= cols)
        self.assertEqual(c.execute("SELECT track FROM jobs WHERE job_key='a'").fetchone()[0], 'internship')
        c.close()

    def test_pay_floor_setting(self):
        from src.hub import db
        self.assertEqual(db.pay_floors(), {'year': 70000, 'hour': 22})
        db.set_setting('uni_pay_floor', {'year': 80000, 'hour': 24})
        self.assertEqual(db.pay_floors()['year'], 80000)


if __name__ == '__main__':
    unittest.main()
