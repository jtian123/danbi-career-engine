"""Transparent ranking and portable feedback. Standard library, Python 3.8+."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
RATINGS = {'interested': 1, 'curious': 0.4, 'not_for_me': -1}
STATUSES = {'not_started', 'saved', 'applied', 'interview', 'offer', 'rejected', 'closed'}
REASONS = {'work', 'technical', 'pay', 'location', 'timing', 'other', ''}


def load(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def canonical_url(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('An ordinary http(s) employer/source URL is required')
    tracking = {'gh_src', 'source', 'src', 'ref', 'referrer', 'in_iframe', 'lang'}
    query = sorted((k, v) for k, v in parse_qsl(p.query) if not k.startswith('utm_') and k not in tracking)
    host = p.netloc.lower()
    if host == 'boards.greenhouse.io':
        host = 'job-boards.greenhouse.io'
    return urlunsplit(('https', host, p.path.rstrip('/'), urlencode(query), ''))


def job_key(job):
    if job.get('requisition_id'):
        return re.sub(r'[^a-z0-9]+', '-', job['company'].lower()).strip('-') + ':' + str(job['requisition_id']).lower()
    return 'url:' + hashlib.sha256(canonical_url(job['url']).encode()).hexdigest()[:20]


def dedupe(jobs):
    seen, out = set(), []
    for j in jobs:
        key = job_key(j)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(j, id=key))
    return out


def iso_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def feedback_adjustments(jobs, feedback):
    """One vote per job, shrink sparse evidence, cap influence at +/-10 points.

    Dislikes for pay/location/timing/technical readiness do not become career dislikes.
    Technical confidence remains descriptive and cannot alter the factual skill profile.
    """
    votes = {}
    for j in jobs:
        f = feedback.get(j['id'], {})
        value = RATINGS.get(f.get('interest'))
        if value is None or (value < 0 and f.get('reason') != 'work'):
            continue
        votes.setdefault(j['lane'], []).append(value)
    return {lane: round(10 * sum(v) / (3 + len(v)), 2) for lane, v in votes.items()}


def assess(job, profile, today=None):
    today = today or date.today()
    blocks, checks = list(job.get('blockers', [])), list(job.get('checks', []))
    if job.get('country') in (None, '', 'unknown'):
        checks.append('Confirm US work location')
    elif job.get('country') != 'US':
        blocks.append('Outside the US search scope')
    if job.get('degree_rule') in ('undergraduate_only', 'phd_only', 'mba_only'):
        blocks.append('Degree enrollment requirement does not match the current MS profile')
    if job.get('citizenship_only') and not profile['work_authorization'].get('us_citizen', False):
        blocks.append('US citizenship required; permanent residence is not citizenship')
    if job.get('technical_level', 0) >= 5:
        blocks.append('Engineering/research intensity exceeds the current search scope')
    end = iso_date(job.get('deadline'))
    if end and end < today:
        blocks.append('Published application deadline has passed')
    if job.get('verification') == 'closed':
        blocks.append('Employer link is closed or unavailable')
    verified = iso_date(job.get('verified_at'))
    if not verified or (today - verified).days > 7 or verified > today:
        checks.append('Recheck employer posting; verification is missing or older than 7 days')
    elif job.get('verification') not in ('employer_live', 'ats_live'):
        checks.append('Live employer application availability needs verification')
    if not job.get('reviewed'):
        checks.append('New discovery: review full requirements and source before recommending')
    graduation = profile['education'][-1].get('expected_graduation')
    if job.get('grad_min') or job.get('grad_max'):
        if not graduation:
            checks.append('Confirm exact graduation month against the employer window')
        elif ((job.get('grad_min') and graduation < job['grad_min']) or
              (job.get('grad_max') and graduation > job['grad_max'])):
            blocks.append('Graduation date falls outside the required window')
    gpa = profile.get('gpa')
    if job.get('min_gpa'):
        if gpa is None:
            checks.append('Confirm GPA is at least ' + str(job['min_gpa']))
        elif gpa < job['min_gpa']:
            blocks.append('GPA is below the required minimum')
    return list(dict.fromkeys(blocks)), list(dict.fromkeys(checks))


def rank(jobs, profile, lanes, feedback=None, today=None):
    today = today or date.today()
    feedback = feedback or {}
    jobs = dedupe(jobs)
    adjustments = feedback_adjustments(jobs, feedback)
    out = []
    for job in jobs:
        j = dict(job)
        blocks, checks = assess(j, profile, today)
        components = j.get('score_components', {})
        # Bounds and names are intentional: published in the dashboard methodology.
        bounds = {'transfer': 30, 'learning': 25, 'readiness': 20, 'pay': 10, 'conversion': 10, 'timing': 5}
        breakdown = {k: min(cap, max(0, float(components.get(k, 0)))) for k, cap in bounds.items()}
        bonus = adjustments.get(j['lane'], 0)
        j.update(score=round(max(0, min(100, sum(breakdown.values()) + bonus)), 1),
                 score_breakdown=breakdown, preference_adjustment=bonus,
                 blockers=blocks, checks=checks, feedback=feedback.get(j['id'], {}))
        if blocks:
            j['bucket'] = 'excluded'
        elif checks:
            j['bucket'] = 'check'
        elif j.get('opportunity_type') == 'talent_pool':
            j['bucket'] = 'pipeline'
        elif j.get('technical_level', 0) >= 4 or j.get('stretch'):
            j['bucket'] = 'stretch'
        else:
            j['bucket'] = 'apply'
        out.append(j)
    return sorted(out, key=lambda j: (-j['score'], j['id']))


def select_today(jobs, limit=6, per_company=2):
    """Keep one exploration slot and cap concentration. Do not fill with ineligible jobs."""
    eligible = [j for j in jobs if j['bucket'] in ('apply', 'stretch') and
                j.get('feedback', {}).get('status', 'not_started') not in ('applied', 'interview', 'offer', 'rejected', 'closed') and
                j.get('feedback', {}).get('interest') != 'not_for_me']
    chosen, counts = [], Counter()
    primary = [j for j in eligible if j['bucket'] == 'apply']
    for j in primary:
        if len(chosen) >= max(0, limit - 1):
            break
        if counts[j['company']] < per_company:
            chosen.append(j)
            counts[j['company']] += 1
    lanes = {j['lane'] for j in chosen}
    remaining = [j for j in eligible if j not in chosen and counts[j['company']] < per_company]
    exploration = [j for j in remaining if j['lane'] not in lanes or j['bucket'] == 'stretch']
    if len(chosen) < limit and (exploration or remaining):
        chosen.append((exploration or remaining)[0])
    return [j['id'] for j in chosen]


def merge_feedback(current, payload, jobs):
    if not isinstance(payload, dict) or payload.get('schema_version') != 1 or payload.get('profile_id') != 'danbi-jang':
        raise ValueError('This is not a Danbi career-engine feedback export')
    records = payload.get('feedback')
    if not isinstance(records, dict):
        raise ValueError('Feedback must be an object keyed by job ID')
    known = {j['id'] for j in dedupe(jobs)}
    merged = dict(current)
    for key, f in records.items():
        if key not in known:
            raise ValueError('Unknown job ID: ' + str(key))
        if not isinstance(f, dict):
            raise ValueError('Invalid feedback record')
        if not all(isinstance(f.get(k, ''), str) for k in ('interest', 'status', 'reason', 'confidence', 'updated_at', 'notes')):
            raise ValueError('Feedback fields must be text')
        if f.get('interest', '') not in ('', *RATINGS) or f.get('status', 'not_started') not in STATUSES:
            raise ValueError('Invalid interest or application status')
        if f.get('reason', '') not in REASONS:
            raise ValueError('Invalid feedback reason')
        if f.get('confidence', '') not in ('', 'ready', 'learn', 'too_technical'):
            raise ValueError('Invalid readiness rating')
        stamp = f.get('updated_at', '')
        try:
            dt = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if dt.tzinfo is None or dt > datetime.now(timezone.utc):
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('Feedback requires a valid past timestamp with time zone')
        old = merged.get(key, {}).get('updated_at')
        if old and datetime.fromisoformat(old.replace('Z', '+00:00')) >= dt:
            continue
        merged[key] = {k: str(f.get(k, ''))[:4000] for k in
                       ('interest', 'reason', 'confidence', 'notes', 'updated_at')}
        merged[key]['status'] = f.get('status', 'not_started')
    return merged


def import_reviewed(current, incoming, lanes):
    """Upsert explicit human/agent reviews. Never infer verification from a scan."""
    if not isinstance(incoming, list) or not incoming:
        raise ValueError('Provide a nonempty JSON array of reviewed job records')
    lane_ids = {l['id'] for l in lanes}
    required_text = ('company', 'title', 'url', 'lane', 'country', 'pay', 'timing',
                     'why', 'gaps', 'task', 'conversion', 'evidence_note', 'next_step')
    reviewed = []
    for source in incoming:
        if not isinstance(source, dict) or source.get('reviewed') is not True:
            raise ValueError('Every imported job must have reviewed=true after reading the full JD')
        j = dict(source)
        if any(not isinstance(j.get(k), str) or not j[k].strip() for k in required_text):
            raise ValueError('Missing required job text fields')
        canonical_url(j['url'])
        if j['lane'] not in lane_ids:
            raise ValueError('Unknown career direction')
        if j.get('verification') not in ('ats_live', 'employer_live', 'employer_indexed', 'secondary', 'closed'):
            raise ValueError('State how the employer/source was verified')
        verified = iso_date(j.get('verified_at'))
        if not verified or verified > date.today():
            raise ValueError('A real, nonfuture verification date is required')
        if not isinstance(j.get('technical_level'), int) or not 1 <= j['technical_level'] <= 5:
            raise ValueError('Technical level must be 1–5')
        if not isinstance(j.get('score_components'), dict):
            raise ValueError('Explicit comparison scores are required')
        for k, cap in {'transfer': 30, 'learning': 25, 'readiness': 20, 'pay': 10, 'conversion': 10, 'timing': 5}.items():
            v = j['score_components'].get(k)
            if not isinstance(v, (float, int)) or not 0 <= v <= cap:
                raise ValueError('Missing or invalid score: ' + k)
        for k in ('checks', 'blockers'):
            if not isinstance(j.get(k), list) or any(not isinstance(v, str) for v in j[k]):
                raise ValueError(k + ' must be an explicit list of text')
        if j.get('degree_rule') not in ('graduate_allowed', 'related_degree', 'unknown', 'undergraduate_only', 'phd_only', 'mba_only'):
            raise ValueError('An explicit degree rule is required')
        if j['degree_rule'] == 'unknown' and not j['checks']:
            raise ValueError('Unknown degree eligibility must include a check')
        reviewed.append(j)
    merged = {job_key(j): j for j in current}
    for j in reviewed:
        merged[job_key(j)] = j
    return list(merged.values())


def render(root=ROOT, output=None, today=None):
    root = Path(root)
    today = today or date.today()
    profile = load(root / 'data/profile.json')
    lanes = load(root / 'data/lanes.json')
    feedback = load(root / 'data/feedback.json', {})
    jobs = rank(load(root / 'data/jobs.json', []), profile, lanes, feedback, today)
    data = {'profile_name': profile['identity']['name'], 'profile_id': 'danbi-jang',
            'built_date': today.isoformat(), 'jobs': jobs, 'lanes': lanes,
            'today_ids': select_today(jobs), 'resources': load(root / 'data/resources.json', []),
            'feedback': feedback, 'coverage': load(root / 'data/coverage.json', {})}
    payload = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    html = (root / 'templates/dashboard.html').read_text().replace('__DATA__', payload)
    output = Path(output or root / 'output' / ('danbi_internships_' + today.isoformat() + '.html'))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html)
    return output, data
