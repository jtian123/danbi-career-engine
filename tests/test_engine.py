import os
from pathlib import Path
if not os.environ.get('DANBI_DATA'):
    os.environ['DANBI_DATA'] = str(Path(__file__).resolve().parents[1] / 'examples' / 'data')
import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from src import engine, paths

DAY = date(2026, 9, 16)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.jobs = engine.load(paths.data('jobs.json'))
        self.profile = engine.load(paths.data('profile.json'))
        self.lanes = engine.load_lanes()
        self.job = copy.deepcopy(self.jobs[0])

    def rank(self, jobs=None, feedback=None, today=DAY):
        return engine.rank(jobs or [self.job], self.profile, self.lanes, feedback, today)

    def export(self, feedback):
        return {'schema_version': 1, 'profile_id': 'danbi-jang', 'feedback': feedback}

    def test_tracking_normalization_preserves_job_identity(self):
        self.assertEqual(engine.canonical_url('https://boards.greenhouse.io/a/jobs/7?utm_source=x'),
                         'https://job-boards.greenhouse.io/a/jobs/7')
        self.assertEqual(engine.canonical_url('https://job-boards.greenhouse.io/a/jobs/7'),
                         'https://job-boards.greenhouse.io/a/jobs/7')
        self.assertIn('gh_jid=9', engine.canonical_url('https://example.com/careers?gh_jid=9&utm_source=x'))
        self.assertNotEqual(engine.job_key({'url': 'https://x.test/jobs?id=1'}),
                            engine.job_key({'url': 'https://x.test/jobs?id=2'}))

    def test_same_title_different_requisition_not_collapsed(self):
        other = dict(self.job, requisition_id='another')
        self.assertEqual(len(engine.dedupe([self.job, other, self.job])), 2)

    def test_unsafe_urls_rejected(self):
        for u in ['javascript:alert(1)', 'file:///tmp/a', 'https://a:b@example.com']:
            with self.assertRaises(ValueError):
                engine.canonical_url(u)

    def test_ineligible_never_enters_today_even_with_max_score(self):
        for changes in ({'degree_rule': 'undergraduate_only'}, {'degree_rule': 'phd_only'},
                        {'country': 'Singapore'}, {'citizenship_only': True}, {'technical_level': 5}):
            j = dict(self.job, **changes)
            ranked = self.rank([j], {engine.job_key(j): {'interest': 'interested'}})
            self.assertEqual(ranked[0]['bucket'], 'excluded')
            self.assertEqual(engine.select_today(ranked), [])

    def test_unknown_location_needs_check_not_false_foreign_claim(self):
        j = dict(self.job, country='unknown')
        r = self.rank([j])[0]
        self.assertEqual(r['bucket'], 'check')
        self.assertFalse(r['blockers'])

    def test_graduation_unknown_then_inside_then_outside(self):
        self.job.update(grad_min='2027-12', grad_max='2028-06')
        self.assertEqual(self.rank()[0]['bucket'], 'check')
        self.profile['education'][-1]['expected_graduation'] = '2028-05'
        self.assertEqual(self.rank()[0]['bucket'], 'apply')
        self.profile['education'][-1]['expected_graduation'] = '2028-09'
        self.assertEqual(self.rank()[0]['bucket'], 'excluded')

    def test_stale_source_and_deadline(self):
        self.assertEqual(self.rank(today=date(2026, 9, 23))[0]['bucket'], 'apply')
        self.assertEqual(self.rank(today=date(2026, 9, 24))[0]['bucket'], 'check')
        self.assertEqual(self.rank(today=date(2026, 9, 28))[0]['bucket'], 'excluded')

    def test_secondary_unreviewed_and_gpa_never_ready(self):
        for changes in ({'verification': 'secondary'}, {'reviewed': False}, {'min_gpa': 3.0}):
            self.assertEqual(self.rank([dict(self.job, **changes)])[0]['bucket'], 'check')

    def test_feedback_idempotent_and_older_import_cannot_revert(self):
        key = engine.job_key(self.job)
        newer = {key: {'interest': 'interested', 'status': 'applied', 'updated_at': '2026-09-15T12:00:00Z'}}
        merged = engine.merge_feedback({}, self.export(newer), self.jobs)
        self.assertEqual(merged, engine.merge_feedback(merged, self.export(newer), self.jobs))
        older = {key: {'interest': 'not_for_me', 'reason': 'work', 'updated_at': '2026-09-14T12:00:00Z'}}
        self.assertEqual(merged, engine.merge_feedback(merged, self.export(older), self.jobs))

    def test_bad_feedback_fails_without_mutating_current(self):
        key = engine.job_key(self.job)
        for record in ({'updated_at': '2099-01-01T00:00:00Z'}, {'updated_at': 1},
                       {'updated_at': '2026-09-15'}, {'updated_at': '2026-09-15T12:00:00Z', 'status': 'submit'},
                       {'updated_at': '2026-09-15T12:00:00Z', 'reason': []}):
            current = {}
            with self.assertRaises(ValueError):
                engine.merge_feedback(current, self.export({key: record}), self.jobs)
            self.assertEqual(current, {})
        with self.assertRaises(ValueError):
            engine.merge_feedback({}, self.export({'not-known': {}}), self.jobs)

    def test_pay_dislike_does_not_penalize_career(self):
        j = self.rank()[0]
        adjustment = engine.feedback_adjustments([j], {j['id']: {'interest': 'not_for_me', 'reason': 'pay'}})
        self.assertEqual(adjustment, {})
        adjustment = engine.feedback_adjustments([j], {j['id']: {'interest': 'not_for_me', 'reason': 'work'}})
        self.assertEqual(adjustment[j['lane']], -2.5)

    def test_feedback_bounds_and_no_manufactured_eligibility(self):
        jobs = [dict(self.job, requisition_id=str(i)) for i in range(100)]
        f = {engine.job_key(j): {'interest': 'interested', 'confidence': 'ready'} for j in jobs}
        adjustments = engine.feedback_adjustments(engine.dedupe(jobs), f)
        self.assertLess(adjustments[self.job['lane']], 10)
        self.job.update(grad_max='2028-01')
        self.assertEqual(self.rank(feedback={engine.job_key(self.job): {'interest': 'interested', 'confidence': 'ready'}})[0]['bucket'], 'check')

    def test_daily_diversity_and_applied_suppression(self):
        r = self.rank(self.jobs)
        ids = engine.select_today(r)
        selected = [j for j in r if j['id'] in ids]
        self.assertGreaterEqual(len(set(j['lane'] for j in selected)), 3)
        self.assertTrue(any(j['bucket'] == 'stretch' for j in selected))
        self.assertLessEqual(max(sum(j['company'] == x['company'] for j in selected) for x in selected), 2)
        f = {key: {'status': 'applied'} for key in ids}
        self.assertFalse(set(ids) & set(engine.select_today(self.rank(self.jobs, f))))

    def test_curated_dataset_passes_review_validation(self):
        self.assertEqual(len(engine.import_reviewed([], self.jobs, self.lanes)), len(self.jobs))

    def test_raw_queue_cannot_be_promoted(self):
        for changes in ({'reviewed': False}, {'verification': 'ats_inventory'}, {'why': ''},
                        {'degree_rule': 'unknown', 'checks': []}):
            with self.assertRaises(ValueError):
                engine.import_reviewed([], [dict(self.job, **changes)], self.lanes)

    def test_review_upserts_requisition_and_preserves_other_history(self):
        edited = dict(self.job, pay='$42/hour')
        merged = engine.import_reviewed(self.jobs, [edited], self.lanes)
        self.assertEqual(len(merged), len(self.jobs))
        self.assertEqual(next(j for j in merged if engine.job_key(j) == engine.job_key(edited))['pay'], '$42/hour')

    def test_embedded_data_cannot_end_script(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'data').mkdir(); (root/'templates').mkdir()
            (root/'templates/dashboard.html').write_text('<script type="application/json">__DATA__</script>')
            for name, value in [('profile', self.profile), ('lanes', self.lanes), ('jobs', [dict(self.job, title='</script><script>alert(1)</script>')])]:
                engine.atomic_json(root/'data'/ (name+'.json'), value)
            path, _ = engine.render(root, today=DAY)
            html = path.read_text()
            self.assertEqual(html.count('</script>'), 1)
            self.assertIn('\\u003c/script', html)


if __name__ == '__main__':
    unittest.main()
