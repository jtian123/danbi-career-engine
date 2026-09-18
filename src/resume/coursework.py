"""Select relevant curriculum without turning course availability into attainment."""
import re

LABELS = {
    'completed': 'Completed coursework',
    'in_progress': 'Coursework in progress',
    'planned_confirmed': 'Planned coursework',
    'program_offering_only': 'Relevant program curriculum',
}
PERSONAL_SOURCES = {'user_confirmation', 'transcript', 'course_registration'}


def validate(profile, evidence):
    courses = profile.get('coursework', [])
    sources = {s['id']: s for s in evidence['sources']}
    schools = {s['id'] for s in profile['education']}
    if len({c['id'] for c in courses}) != len(courses):
        raise ValueError('Duplicate course IDs')
    for c in courses:
        if c['school_id'] not in schools or c['status'] not in LABELS:
            raise ValueError('Unknown course school or status')
        if not c.get('evidence') or not set(c['evidence']).issubset(sources):
            raise ValueError('Course needs curriculum evidence')
        if c['status'] != 'program_offering_only':
            personal = c.get('status_evidence', [])
            if not personal or any(s not in sources or sources[s].get('kind') not in PERSONAL_SOURCES or
                                   sources[s].get('confirmed_course_statuses', {}).get(c['id']) != c['status']
                                   for s in personal):
                raise ValueError('Personal course status needs confirmation; a catalog or homework template is insufficient')
    return True


def select(profile, cfg, jd, lane):
    words = set(re.findall(r'[a-z0-9]+', jd.lower()))
    ranked = []
    for c in profile.get('coursework', []):
        overlap = len(words & set(re.findall(r'[a-z0-9]+', ' '.join(c['tags']).lower())))
        relevance = 4 * (lane in c['lanes']) + overlap
        if relevance:
            personal = c['status'] in ('completed', 'in_progress')
            ranked.append((-(relevance + 2 * personal), c['id']))
    return [ident for _, ident in sorted(ranked)[:cfg.get('max_courses', 3)]]


def compile_selected(ids, profile, cfg):
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
        raise ValueError('coursework_ids must be a list of course IDs')
    if len(ids) != len(set(ids)) or len(ids) > cfg.get('max_courses', 3):
        raise ValueError('Choose at most three distinct relevant courses')
    courses = {c['id']: c for c in profile.get('coursework', [])}
    schools = {e['id']: e['school'] for e in profile['education']}
    groups, trace = {}, []
    for ident in ids:
        if ident not in courses:
            raise ValueError('Unknown course: ' + ident)
        c = courses[ident]
        title = c['code'] + ' ' + c['title']
        groups.setdefault(c['school_id'], {}).setdefault(c['status'], []).append(title)
        trace.append({'claim_id': 'course:' + ident, 'variant': c['status'], 'kind': 'coursework',
                      'company': schools[c['school_id']], 'text': title,
                      'evidence': list(dict.fromkeys(c['evidence'] + c.get('status_evidence', []))),
                      'caveat': 'Rendered under ' + LABELS[c['status']] + '. ' + c.get('caveat', '')})
    lines = {school: [LABELS[status] + ': ' + '; '.join(titles) for status, titles in rows.items()]
             for school, rows in groups.items()}
    return lines, trace
