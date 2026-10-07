"""Resume content compiler and audit gates. No credentials, network, or borrowed identity.

The writer chooses source-backed claim variants. New prose belongs in the reviewed
master profile first; a JD, draft plan or reviewer cannot introduce new facts.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
from pathlib import Path

from ..engine import ROOT, load
from . import coursework


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bundle(root=None):
    from .. import paths
    d = (lambda *p: Path(root).joinpath('data', *p)) if root else paths.data
    profile = load(d('master_profile.json'))
    config = load(paths.registry('resume_config.json'))
    evidence = load(d('resume', 'evidence.json'))
    if profile is None or evidence is None:
        raise FileNotFoundError('Danbi\'s private résumé facts are missing from data/ — unzip her private bundle '
                                'into data/ (see SETUP_FOR_CLAUDE.md)')
    validate_profile(profile, evidence)
    return profile, config, evidence


def validate_profile(profile, evidence):
    if profile.get('profile_id') != 'danbi-jang' or profile.get('identity', {}).get('name') != 'Danbi Jang':
        raise ValueError('A Danbi master profile is required')
    source_ids = {s['id'] for s in evidence['sources']}
    if len(source_ids) != len(evidence['sources']):
        raise ValueError('Duplicate evidence IDs')
    claims = [c for e in profile['experience'] for c in e['bullets']]
    if len({c['id'] for c in claims}) != len(claims):
        raise ValueError('Claim IDs must be unique')
    for obj in claims + profile['skills'] + profile['education'] + profile['experience'] + list(profile['summary_variants'].values()) + [profile['identity']]:
        if not obj.get('evidence') or not set(obj['evidence']).issubset(source_ids):
            raise ValueError('Missing or unknown evidence on ' + str(obj.get('id', 'profile field')))
    for e in profile['experience']:
        if e['title'] not in e['title_aliases']:
            raise ValueError('Canonical title is not in the allowed title list')
        for c in e['bullets']:
            if c['status'] == 'usable' and (not c.get('variants') or any(not isinstance(v, str) or not v.strip() for v in c['variants'].values())):
                raise ValueError('Usable claims require reviewed wording')
            if e['id'] in ('apateu', 'xresearch') and c['status'] == 'usable':
                if c['category'] not in ('product', 'design', 'growth', 'research', 'strategy', 'operations') or 'user_scope_20260918' not in c['evidence']:
                    raise ValueError('Founder claims require nontechnical scope and user attribution')
    coursework.validate(profile, evidence)
    return True


def tokens(text):
    return set(re.findall(r'[a-z0-9]+', text.lower()))


def starter_plan(profile, cfg, jd, lane):
    """An explicit draft baseline; the assistant writer must assess emphasis afterward."""
    if lane not in cfg['headlines']:
        raise ValueError('Unknown resume lane: ' + lane)
    target_words = tokens(jd)
    role_ids = ['apateu', 'silicon2']
    if lane in cfg['include_secondary_lanes']:
        role_ids.append('xresearch')
    experiences = []
    for ident in role_ids:
        e = next(e for e in profile['experience'] if e['id'] == ident)
        ranked = sorted((c for c in e['bullets'] if c['status'] == 'usable'),
                        key=lambda c: -(6 * (lane in c['lanes']) + len(tokens(' '.join(c['tags'])) & target_words)))
        count = 5 if ident == 'apateu' else 2 if ident == 'xresearch' else 3
        title = e['title']
        if ident == 'apateu' and lane == 'product':
            title = 'Co-Founder, Product & Design'
        experiences.append({'id': ident, 'title': title,
                            'bullets': [{'claim_id': c['id'], 'variant': 'standard'} for c in ranked[:count]]})
    skills = [s for s in profile['skills'] if s['status'] == 'usable' and lane in s['lanes']]
    skills.sort(key=lambda s: -(4 * (s['group'] == 'Business') + len(tokens(s['text']) & target_words)))
    selected, groups = [], {}
    for s in skills:
        # Keep the three useful groups; language proficiency stays in the profile.
        if s['group'] == 'Languages' or (s['id'] in ('photoshop', 'canva') and lane not in ('commerce', 'product')):
            continue
        if groups.get(s['group'], 0) >= (4 if s['group'] == 'Business' else 3):
            continue
        selected.append(s['id']); groups[s['group']] = groups.get(s['group'], 0) + 1
    return {'schema_version': 1, 'lane': lane, 'summary_key': lane, 'experience': experiences,
            'skill_ids': selected, 'coursework_ids': coursework.select(profile, cfg, jd, lane),
            'writer_notes': 'Starter selection. Review JD relevance, redundancy and genuine gaps before release.'}


def compile_resume(plan, profile, cfg):
    """Only known IDs and reviewed wording render; arbitrary draft text is rejected."""
    if not isinstance(plan, dict) or plan.get('schema_version') != 1:
        raise ValueError('Invalid writer-plan schema')
    allowed = {'schema_version', 'lane', 'summary_key', 'experience', 'skill_ids', 'coursework_ids', 'writer_notes'}
    if set(plan) - allowed:
        raise ValueError('Writer plans select facts; add new wording to the master profile before using it')
    lane = plan['lane']
    if lane not in cfg['headlines'] or plan['summary_key'] not in profile['summary_variants']:
        raise ValueError('Unknown lane or summary')
    summary = profile['summary_variants'][plan['summary_key']]
    if summary['status'] != 'usable':
        raise ValueError('Summary is not approved for use')
    exps, trace, ids = [], [], []
    source_roles = {e['id']: e for e in profile['experience']}
    for row in plan['experience']:
        if set(row) != {'id', 'title', 'bullets'} or row['id'] not in source_roles or row['id'] in ids:
            raise ValueError('Unknown, duplicate or malformed experience')
        e = source_roles[row['id']]; ids.append(e['id'])
        if row['title'] not in e['title_aliases']:
            raise ValueError('Unsupported title for ' + e['company'])
        choices = {c['id']: c for c in e['bullets']}
        bullets, seen = [], set()
        for selection in row['bullets']:
            if set(selection) != {'claim_id', 'variant'}:
                raise ValueError('A bullet must select a reviewed claim variant')
            cid, variant = selection['claim_id'], selection['variant']
            c = choices.get(cid)
            if not c or c['status'] != 'usable' or cid in seen or variant not in c['variants']:
                raise ValueError('Unknown, duplicated, quarantined or wrong-role claim: ' + str(cid))
            seen.add(cid); bullets.append(c['variants'][variant])
            trace.append({'claim_id': cid, 'variant': variant, 'company': e['company'], 'text': c['variants'][variant],
                          'evidence': c['evidence'], 'caveat': c.get('caveat', '')})
        if not bullets:
            raise ValueError('Every experience needs at least one supported bullet')
        # An unresolved historical end date is omitted, never silently rendered Present.
        dates = date_range(e['start'], e['end']) if e.get('start') and e.get('end') else ''
        exps.append({'id': e['id'], 'company': e['company'], 'title': row['title'], 'location': e['location'],
                     'dates': dates, 'bullets': bullets})
    skill_map = {s['id']: s for s in profile['skills']}
    groups, skill_seen = {}, set()
    for sid in plan['skill_ids']:
        s = skill_map.get(sid)
        if not s or s['status'] != 'usable' or sid in skill_seen:
            raise ValueError('Unknown, duplicate or unsupported skill: ' + str(sid))
        skill_seen.add(sid)
        groups.setdefault(s['group'], []).append(s['text'])
    course_lines, course_trace = coursework.compile_selected(plan.get('coursework_ids', []), profile, cfg)
    education = [{k: e[k] for k in ('school', 'degree', 'dates', 'gpa') if e.get(k) or k != 'gpa'}
                 for e in profile['education']]
    for source, rendered in zip(profile['education'], education):
        if source['id'] in course_lines:
            rendered['coursework'] = course_lines[source['id']]
    trace.extend(course_trace)
    resume = {'profile_id': 'danbi-jang', 'lane': lane, 'name': profile['identity']['name'],
              'headline': cfg['headlines'][lane], 'contact': copy.deepcopy(profile['identity']['contact']),
              'summary': summary['text'], 'education': education,
              'experience': exps, 'skills': [{'group': g, 'items': items} for g, items in groups.items()],
              'product_links': profile['product_links']}
    return resume, trace


def date_range(start, end):
    def fmt(s):
        if s == 'Present':
            return s
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', s):
            raise ValueError('Experience dates must be YYYY-MM or Present')
        return ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][int(s[5:])-1] + ' ' + s[:4]
    return fmt(start) + ' - ' + fmt(end)


def visible_text(resume):
    fields = [resume['name'], resume['headline'], resume['summary'], *resume['contact'].values()]
    for ed in resume['education']:
        fields.extend(ed[k] for k in ('school', 'degree', 'dates'))
        fields.extend(ed.get('coursework', []))
    for e in resume['experience']:
        fields.extend([e['company'], e['title'], e['location'], e['dates'], *e['bullets']])
    for g in resume['skills']:
        fields.extend([g['group'], *g['items']])
    return '\n'.join(str(x) for x in fields)


def qa(resume, trace, profile, cfg):
    blockers, majors, submission = [], [], []
    exps = resume['experience']; ids = [e['id'] for e in exps]
    if not cfg['min_experiences'] <= len(exps) <= cfg['max_experiences']:
        majors.append('Use two or three experiences')
    if 'apateu' not in ids:
        majors.append('Apateu must be an anchor')
    if 'xresearch' in ids and ('apateu' not in ids or ids.index('xresearch') < ids.index('apateu')):
        majors.append('Apateu must precede XResearch')
    lengths = [len(e['bullets']) for e in exps]
    if lengths and lengths[0] < max(lengths):
        majors.append('The lead role must have the most detail')
    if sum(lengths) > cfg['max_bullets']:
        majors.append('Bullet count exceeds configured cap')
    for i, e in enumerate(exps):
        cap = cfg['max_secondary_bullets'] if e['id'] == 'xresearch' else cfg['max_primary_bullets'] if i == 0 else cfg['max_other_bullets']
        if len(e['bullets']) > cap:
            majors.append('Too many bullets for ' + e['company'])
        if not e['dates']:
            submission.append('Confirm ' + e['company'] + ' employment end date; conflicting old resumes are recorded in the profile')
    if 'xresearch' in ids and 'apateu' in ids and lengths[ids.index('xresearch')] >= lengths[ids.index('apateu')]:
        majors.append('XResearch must have less detail than Apateu')
    if len(resume['skills']) > cfg['max_skill_groups'] or any(len(g['items']) > cfg['max_skills_per_group'] for g in resume['skills']):
        majors.append('Skills section exceeds configured caps')
    words = len(visible_text(resume).split())
    if words > cfg['max_words']:
        majors.append('Word budget exceeded')
    text = visible_text(resume)
    for pattern, label in [(r'\b(?:James|Jinyao)\b|jtian123|jamestian', 'Another person leaked into the resume'),
                           (r'<[^>]+>|\[[^\]]+\]\(', 'HTML or Markdown residue'),
                           (r'(?i)\b(?:CTO|CEO|Chief|Senior|Head of|Lead Engineer)\b', 'Unsupported seniority or engineering positioning')]:
        if re.search(pattern, text):
            blockers.append(label)
    body = resume['summary'] + '\n' + '\n'.join(c['text'] for c in trace)
    if re.search(r'https?://', body):
        blockers.append('Product links belong on product names, not raw URLs in bullets')
    if re.search(r'\[[^\]]{1,60}\]', text):
        blockers.append('Bracket residue such as [Name] in the visible text')
    warnings = []
    for where, line in [('headline', resume.get('headline', '')), ('summary', resume.get('summary', ''))] + \
            [(e['company'], b) for e in exps for b in e['bullets']]:
        warnings += [f'{where}: {w}' for w in plain_language(line, cfg)]
    if re.search(r'\b\d+\+?\s*(?:years?|yrs?)\b', resume.get('summary', '') + ' ' + resume.get('headline', ''), re.I):
        warnings.append('summary/headline: a years-of-experience number — make sure it describes the right KIND of work '
                        '(e-commerce marketing ≠ analytics ≠ product), not just the right total')
    return {'blockers': blockers, 'majors': majors, 'submission_blockers': submission, 'warnings': warnings,
            'word_count': words, 'status': 'pass' if not blockers and not majors else 'revise',
            'note': 'Deterministic source and structure checks. Editorial and visual reviews are separate required stages.'}


AI_TELLS = r'\b(leverag\w*|robust|decision-grade|end[- ]to[- ]end|spearhead\w*|synerg\w*|cutting-edge|utiliz\w*|' \
           r'seamless\w*|holistic|best-in-class|world-class|delv\w*|game-changing|state-of-the-art)\b'
STATUS_WORDS = r'\b(shipped|deployed|in production|production-grade|scaled to|launched)\b'


def plain_language(line, cfg=None):
    """说人话 — the reader test, made into warnings (never blockers). A recruiter outside
    the company should be able to say back what was done and why in one sentence."""
    out = []
    words = len(line.split())
    if words > 32:
        out.append(f'{words} words — one idea per bullet, aim for ≤ 30')
    if re.search(r'→|->|=>', line):
        out.append('arrow chain — write it as a sentence')
    if line.count('—') + line.count(' – ') > 1:
        out.append('more than one dash — split or simplify')
    m = re.search(AI_TELLS, line, re.I)
    if m:
        out.append(f'"{m.group(0)}" reads as filler — say what actually happened')
    if re.search(r'(?:[^,;]+,){4,}[^,;]+', line):
        out.append('long list — keep the 2–3 specifics a reader cares about')
    for term in ((cfg or {}).get('plain_language') or {}).get('avoid_terms', []):
        if re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', line, re.I):
            out.append(f'in-house or spec term "{term}" — translate it for an outsider')
    m = re.search(STATUS_WORDS, line, re.I)
    if m:
        out.append(f'"{m.group(0)}" claims a status — confirm the source supports it')
    if re.search(r'\bDanbi\b', line):
        out.append('third person — the résumé never names its owner in the body')
    return out


def lint_bank(profile, cfg):
    """Plain-language warnings for every usable wording variant in the claim bank."""
    rows = []
    for role in profile.get('experience', []):
        for c in role.get('bullets', []):
            if c.get('status') != 'usable':
                continue
            for name, text in (c.get('variants') or {}).items():
                issues = plain_language(text, cfg)
                if issues:
                    rows.append({'claim_id': c['id'], 'variant': name, 'issues': issues})
    return rows


def trim_once(plan, cfg):
    result = copy.deepcopy(plan)
    for e in reversed(result['experience']):
        for b in reversed(e['bullets']):
            if b['variant'] == 'standard':
                b['variant'] = 'concise'
                return result, 'Used concise wording for ' + b['claim_id']
    for i in range(len(result['experience'])-1, -1, -1):
        e = result['experience'][i]
        minimum = 3 if i == 0 else 2
        if len(e['bullets']) > minimum:
            dropped = e['bullets'].pop()
            return result, 'Removed lower-priority bullet ' + dropped['claim_id']
        if len(result['experience']) > cfg['min_experiences'] and i == len(result['experience'])-1:
            dropped = result['experience'].pop()
            return result, 'Removed optional role ' + dropped['id']
    raise ValueError('Cannot fit one page without losing the required experience depth. Rewrite approved variants instead of shrinking type.')


def review_templates(resume, trace, profile, jd, artifacts):
    subject = {'resume_sha256': digest(resume), 'profile_sha256': digest(profile), 'jd_sha256': digest(jd)}
    content = dict(schema_version=1, review_kind='content', **subject, reviewer='', verdict='revise',
                   blockers=[], majors=[], summary='', jd_alignment=None,
                   claim_reviews=[{'claim_id': t['claim_id'], 'supported': False, 'reason': ''} for t in trace],
                   checks={k: False for k in ('identity_dates_titles', 'skill_levels', 'scope_and_metrics', 'no_engineering_ownership', 'jd_relevance', 'no_redundancy', 'coursework_status')},
                   genuine_gaps=[])
    visual = dict(schema_version=1, review_kind='visual', **subject, reviewer='', verdict='revise',
                  artifacts=artifacts, pages_reviewed=[],
                  checks={k: False for k in ('one_page', 'legible_type', 'no_clipping_or_overlap', 'balanced_spacing', 'working_links', 'clean_text_order')}, notes='')
    return content, visual


def validate_review(review, kind, resume, trace, profile, jd, artifacts=None):
    if not isinstance(review, dict) or review.get('schema_version') != 1 or review.get('review_kind') != kind:
        raise ValueError('Missing or malformed ' + kind + ' review')
    if any(review.get(k) != v for k, v in {'resume_sha256': digest(resume), 'profile_sha256': digest(profile), 'jd_sha256': digest(jd)}.items()):
        raise ValueError('Review is stale: content, profile or JD changed')
    if review.get('verdict') != 'pass' or not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
        raise ValueError('A named reviewer must pass the current ' + kind + ' review')
    required = ('identity_dates_titles', 'skill_levels', 'scope_and_metrics', 'no_engineering_ownership', 'jd_relevance', 'no_redundancy', 'coursework_status') if kind == 'content' else ('one_page', 'legible_type', 'no_clipping_or_overlap', 'balanced_spacing', 'working_links', 'clean_text_order')
    checks = review.get('checks', {})
    if not isinstance(checks, dict) or any(checks.get(k) is not True for k in required):
        raise ValueError('Every review check must be explicitly true')
    if kind == 'content':
        rows = review.get('claim_reviews', [])
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError('Invalid claim reviews')
        if {r.get('claim_id') for r in rows} != {t['claim_id'] for t in trace} or len(rows) != len(trace):
            raise ValueError('Every rendered claim must be reviewed exactly once')
        if any(r.get('supported') is not True or not isinstance(r.get('reason'), str) or not r['reason'].strip() for r in rows):
            raise ValueError('Every claim needs an explicit supported verdict and reason')
        if review.get('blockers') != [] or review.get('majors') != [] or not review.get('summary'):
            raise ValueError('Open content findings must be resolved')
        if not isinstance(review.get('genuine_gaps'), list) or not isinstance(review.get('jd_alignment'), int) or not 0 <= review['jd_alignment'] <= 100:
            raise ValueError('Include genuine gaps and an editorial alignment assessment')
    else:
        if review.get('pages_reviewed') != [1] or not review.get('notes') or review.get('artifacts') != artifacts:
            raise ValueError('Visual review must cover the exact current one-page artifacts')
    return True


def cleanest(candidates):
    """Reviewed candidate selection: factual/major regressions cannot win on keyword fit."""
    if not candidates:
        raise ValueError('No reviewed candidates')
    def key(c):
        report = c['report']
        return (len(report['blockers']), len(report['majors']), -report.get('jd_alignment', 0))
    return min(candidates, key=key)
