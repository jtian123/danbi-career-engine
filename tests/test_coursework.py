"""Course relevance must never turn curriculum availability into attainment."""
import copy
import unittest

from src.resume import core, coursework


class CourseworkTests(unittest.TestCase):
    def setUp(self):
        self.profile, self.cfg, self.evidence = core.bundle()
        self.jd = 'Product internship: customer research, experiment design, decision making and analytics.'
        self.plan = core.starter_plan(self.profile, self.cfg, self.jd, 'product')

    def course(self, profile=None, ident='ise529'):
        return next(c for c in (profile or self.profile)['coursework'] if c['id'] == ident)

    def compile(self, ids):
        plan = copy.deepcopy(self.plan)
        plan['coursework_ids'] = ids
        return core.compile_resume(plan, self.profile, self.cfg)

    def test_catalog_is_not_personal_enrollment(self):
        self.assertEqual(
            {c['id'] for c in self.profile['coursework']},
            {'ise521', 'ise529', 'ise530', 'ise534', 'ise558', 'ise525',
             'ise562', 'ise580', 'ise537', 'ise540'},
        )
        self.assertTrue(all(c['status'] == 'program_offering_only'
                            for c in self.profile['coursework']))
        self.assertTrue(core.validate_profile(self.profile, self.evidence))

    def test_selection_changes_with_job_and_remains_capped(self):
        # Keep lane constant so this proves the job text participates in selection.
        jobs = (
            'Financial investment portfolio risk equity finance financial analytics',
            'Experiments experimentation experimental design testing data collection',
            'Simulation operations process performance optimization allocation',
        )
        selections = [coursework.select(self.profile, self.cfg, jd, 'product') for jd in jobs]
        self.assertGreater(len({tuple(ids) for ids in selections}), 1)
        for ids in selections:
            self.assertLessEqual(len(ids), 3)
            self.assertEqual(len(ids), len(set(ids)))

    def test_course_titles_render_under_usc_with_honest_catalog_label(self):
        resume, _ = self.compile(['ise529', 'ise525'])
        usc = next(ed for ed in resume['education']
                   if ed['school'] == 'University of Southern California')
        self.assertEqual(len(usc['coursework']), 1)
        self.assertTrue(usc['coursework'][0].startswith('Relevant program curriculum: '))
        for ident in ('ise529', 'ise525'):
            c = self.course(ident=ident)
            self.assertIn(c['code'] + ' ' + c['title'], usc['coursework'][0])
        ucla = next(ed for ed in resume['education']
                    if ed['school'] == 'University of California, Los Angeles')
        self.assertFalse(ucla.get('coursework'))
        self.assertIn(usc['coursework'][0], core.visible_text(resume))

    def test_unknown_duplicate_excess_and_freeform_course_selections_rejected(self):
        for ids in (['invented'], ['ise529', 'ise529'],
                    ['ise521', 'ise529', 'ise530', 'ise525'],
                    'ise529', [{'id': 'ise529', 'status': 'completed'}]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.compile(ids)

    def test_catalog_and_homework_cannot_establish_personal_status(self):
        for status in ('completed', 'in_progress', 'planned_confirmed'):
            for kind in ('official_curriculum', 'homework_template'):
                with self.subTest(status=status, kind=kind):
                    profile, evidence = copy.deepcopy(self.profile), copy.deepcopy(self.evidence)
                    source_id = 'fixture_nonpersonal_source'
                    evidence['sources'].append({'id': source_id, 'kind': kind})
                    course = self.course(profile)
                    course['status'] = status
                    course['status_evidence'] = [source_id]
                    with self.assertRaises(ValueError):
                        core.validate_profile(profile, evidence)

    def test_missing_confirmation_blocks_every_personal_status(self):
        for status in ('completed', 'in_progress', 'planned_confirmed'):
            with self.subTest(status=status):
                profile = copy.deepcopy(self.profile)
                course = self.course(profile)
                course['status'] = status
                course['status_evidence'] = []
                with self.assertRaises(ValueError):
                    core.validate_profile(profile, self.evidence)

    def test_explicit_personal_confirmation_allows_accurately_labeled_status(self):
        labels = {'completed': 'Completed coursework',
                  'in_progress': 'Coursework in progress',
                  'planned_confirmed': 'Planned coursework'}
        for status, label in labels.items():
            with self.subTest(status=status):
                profile, evidence = copy.deepcopy(self.profile), copy.deepcopy(self.evidence)
                evidence['sources'].append({
                    'id': 'fixture_course_confirmation', 'kind': 'user_confirmation',
                    'text': 'Test fixture: user explicitly confirms Danbi ISE 529 status: ' + status,
                    'confirmed_course_statuses': {'ise529': status},
                })
                course = self.course(profile)
                course['status'] = status
                course['status_evidence'] = ['fixture_course_confirmation']
                self.assertTrue(core.validate_profile(profile, evidence))
                lines, trace = coursework.compile_selected(['ise529'], profile, self.cfg)
                self.assertTrue(lines['usc'][0].startswith(label + ': '))
                self.assertIn('fixture_course_confirmation', trace[0]['evidence'])
                self.assertEqual(trace[0]['variant'], status)

    def test_generic_or_wrong_course_confirmation_cannot_establish_status(self):
        for mapping in (None, {'ise521': 'completed'}, {'ise529': 'in_progress'}):
            with self.subTest(mapping=mapping):
                profile, evidence = copy.deepcopy(self.profile), copy.deepcopy(self.evidence)
                source = {'id': 'fixture_generic_confirmation', 'kind': 'user_confirmation',
                          'text': 'Danbi is a current USC student.'}
                if mapping is not None:
                    source['confirmed_course_statuses'] = mapping
                evidence['sources'].append(source)
                course = self.course(profile)
                course['status'] = 'completed'
                course['status_evidence'] = [source['id']]
                with self.assertRaises(ValueError):
                    core.validate_profile(profile, evidence)

    def test_course_selection_cannot_promote_skills(self):
        plain, _ = self.compile([])
        selected, _ = self.compile(['ise521', 'ise529', 'ise558'])
        self.assertEqual(selected['skills'], plain['skills'])
        skills = ' '.join(item for group in selected['skills'] for item in group['items'])
        self.assertIn('SQL fundamentals (learning)', skills)
        self.assertIn('Python fundamentals (learning)', skills)
        for unsupported in ('Tableau', 'Machine Learning', 'Scikit', 'Airflow', 'Spark'):
            self.assertNotIn(unsupported, skills)

    def test_course_claims_require_source_and_status_review(self):
        resume, trace = self.compile(['ise529', 'ise525'])
        course_claims = {t['claim_id'] for t in trace if t.get('kind') == 'coursework'}
        self.assertEqual(course_claims, {'course:ise529', 'course:ise525'})
        for row in trace:
            if row['claim_id'] in course_claims:
                self.assertTrue(row['evidence'])
                self.assertIn('Relevant program curriculum', row['caveat'])
        review, _ = core.review_templates(resume, trace, self.profile, self.jd, {})
        review.update(reviewer='Coursework fixture reviewer', verdict='pass',
                      summary='Every rendered fixture claim and course status checked.', jd_alignment=75)
        review['checks'] = {key: True for key in review['checks']}
        review['claim_reviews'] = [
            {'claim_id': t['claim_id'], 'supported': True, 'reason': 'Fixture source checked.'}
            for t in trace
        ]
        self.assertTrue(core.validate_review(review, 'content', resume, trace, self.profile, self.jd))
        omitted = copy.deepcopy(review)
        omitted['claim_reviews'] = [r for r in omitted['claim_reviews']
                                    if r['claim_id'] != 'course:ise529']
        with self.assertRaises(ValueError):
            core.validate_review(omitted, 'content', resume, trace, self.profile, self.jd)
        review['checks']['coursework_status'] = False
        with self.assertRaises(ValueError):
            core.validate_review(review, 'content', resume, trace, self.profile, self.jd)

    def test_invalid_school_status_and_duplicate_catalog_ids_rejected(self):
        for field, value in (('school_id', 'invented'), ('status', 'proficient')):
            with self.subTest(field=field):
                profile = copy.deepcopy(self.profile)
                self.course(profile)[field] = value
                with self.assertRaises(ValueError):
                    core.validate_profile(profile, self.evidence)
        profile = copy.deepcopy(self.profile)
        profile['coursework'].append(copy.deepcopy(profile['coursework'][0]))
        with self.assertRaises(ValueError):
            core.validate_profile(profile, self.evidence)


if __name__ == '__main__':
    unittest.main()
