"""`career.py scan` and `career.py mark`.

scan:  harvest every public source → keep US internships in scope → read direction,
       industry, season and eligibility lines → merge duplicates (employer link wins)
       → drop what she has already seen or acted on → pre-rank (big employers first)
       → write a review queue with full posting text for Claude.
mark:  validate Claude's reviews, put the day's list into the hub, remember what was
       shown. Unreviewed candidates go in too, as "score unknown" (never silently lost).

Nothing here applies, emails, logs in, or bypasses a block.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from .. import paths
from ..engine import atomic_json, canonical_url, iso_date
from . import classify as C
from . import net, sources, state

SOURCE_RANK = {'ats': 0, 'bigtech': 1, 'simplify': 2, 'linkedin': 3, 'lead': 4}
CORE_LANES = ('commerce', 'insights', 'product', 'strategy', 'business', 'demand', 'operations')
PASSING_SEASONS = ('Summer 2027', 'Spring 2027', 'Winter 2027', 'Fall 2027', 'Winter 2026')


def _load(name):
    return json.loads(paths.registry(name).read_text())


def _say(log, msg):
    log(msg)


# ------------------------------------------------------------------ harvest
def _tasks(general_boards=True):
    ent = _load('enterprises.json')
    tasks = []
    for e in ent['employers']:
        for a in e.get('ats', []):
            if a.get('verified') is False:
                continue
            tasks.append((f"{a['type']}:{a.get('token') or a.get('tenant') or a.get('domain') or a.get('host') or a.get('refNum') or a.get('site') or e['name']}",
                          'enterprise', e['name'], a))
    if general_boards:
        for b in _load('boards.json')['boards']:
            tasks.append((f"{b['type']}:{b['token']}", 'board', b.get('company') or '', b))
    tasks.append(('simplify', 'list', '', {'type': 'simplify'}))
    return tasks


ADAPTERS = {'greenhouse': sources.greenhouse, 'lever': sources.lever, 'ashby': sources.ashby,
            'smartrecruiters': sources.smartrecruiters, 'workday': sources.workday,
            'eightfold': sources.eightfold, 'oracle': sources.oracle, 'simplify': sources.simplify,
            'jibe': sources.jibe, 'phenom': sources.phenom, 'jobscore': sources.jobscore,
            **sources.BIGTECH}


def _run_task(task):
    board_id, kind, company, a = task
    fn = ADAPTERS[a['type']]
    params = {k: v for k, v in a.items() if k not in ('type', 'verified', 'note', 'company')}
    rows, inventory = fn(company=company, **params)
    for r in rows:
        r['origin_kind'] = kind
        if kind == 'enterprise':
            r['company'] = company
    return rows, inventory


def harvest(general_boards=True, use_linkedin=True, workers=16, log=print, today=None):
    today = today or date.today()
    board_state = state.boards()
    tasks = [t for t in _tasks(general_boards) if state.board_active(board_state.get(t[0], {}), today)]
    rows, health, results = [], {}, {}
    _say(log, f'[scan] {len(tasks)} sources to read (+ LinkedIn)' if use_linkedin else f'[scan] {len(tasks)} sources')
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_run_task, t): t for t in tasks}
        li_future = ex.submit(_linkedin, today, log) if use_linkedin else None
        done = 0
        for f in as_completed(futs):
            board_id, kind, company, a = futs[f]
            lane = 'enterprise' if kind == 'enterprise' else ('simplify' if kind == 'list' else
                                                               'bigtech' if a['type'] in sources.BIGTECH else 'boards')
            h = health.setdefault(lane, {'sources': 0, 'ok': 0, 'failed': 0, 'blocked': 0, 'inventory': 0, 'rows': 0})
            h['sources'] += 1
            try:
                got, inv = f.result()
                rows += got
                h['ok'] += 1
                h['inventory'] += inv or 0
                h['rows'] += len(got)
                results[board_id] = True
            except net.Blocked:
                h['blocked'] += 1
                results[board_id] = False
            except Exception as e:  # noqa: BLE001 — one dead board never stops the scan
                h['failed'] += 1
                h.setdefault('errors', []).append(f'{board_id}: {type(e).__name__}: {str(e)[:80]}')
                results[board_id] = False
            done += 1
            if done % 200 == 0:
                _say(log, f'[scan] {done}/{len(tasks)} sources read')
        if li_future:
            li_rows, li_health = li_future.result()
            rows += li_rows
            health['linkedin'] = li_health
    state.record_boards(results, today.isoformat())
    for h in health.values():
        if 'errors' in h:
            h['errors'] = h['errors'][:12]
    return rows, health


def _linkedin(today, log):
    li = state.linkedin()
    if li.get('blocked_on') == today.isoformat():
        return [], {'status': 'skipped', 'note': 'blocked earlier today'}
    base = [q for lane in _load('lanes.json')['lanes'] for q in lane.get('queries', [])]
    base += _load('queries.json').get('extra', [])
    ranked = state.ranked_queries(list(dict.fromkeys(base)))
    off = li.get('offset', 0) % max(1, len(ranked))
    window = (ranked[off:] + ranked[:off])[:10]
    try:
        got, meta = sources.linkedin(window, log=log)
    except net.Blocked as e:
        li.setdefault('blocks', []).append({'day': today.isoformat(), 'why': str(e)})
        li['blocks'] = li['blocks'][-20:]
        li['blocked_on'] = today.isoformat()
        state.save_linkedin(li)
        return [], {'status': 'blocked', 'note': f'{e} — skipped for today, not retried'}
    except Exception as e:  # noqa: BLE001
        return [], {'status': 'failed', 'note': f'{type(e).__name__}: {e}'}
    li['offset'] = (off + 10) % max(1, len(ranked))   # persist only after a clean pass
    state.save_linkedin(li)
    state.add_query_yield(meta['by_query'], today.isoformat())
    return got, {'status': 'ok', 'requests': meta['requests'], 'rows': len(got), 'queries': window}


# ------------------------------------------------------------------ filter + classify
def _lead_profile():
    p = paths.data('profile.json')
    return json.loads(p.read_text()) if p.exists() else {}


def classify_rows(rows, today, never=frozenset(), citizen=None):
    """-> (kept, dropped_counter). Every drop has a counted reason. Eligibility follows
    data/profile.json → work_authorization (sponsorship language is never a filter)."""
    if citizen is None:
        citizen = bool(_lead_profile().get('work_authorization', {}).get('us_citizen'))
    kept, dropped = [], Counter()
    for r in rows:
        title = r['title']
        if not title or not r.get('url'):
            dropped['no title or link'] += 1
            continue
        if not (r.get('intern_hint') or C.INTERN.search(title)) or re.search(r'\bfellowship|new grad', title, re.I):
            dropped['not an internship'] += 1
            continue
        why = C.out_of_scope(title)
        if why:
            dropped['out of scope: ' + why] += 1
            continue
        if C.is_staffing(r['company']) or C.canon_company(r['company']) in never:
            dropped['staffing agency or never-show'] += 1
            continue
        where = C.us_status(r['location'])
        if where == 'foreign':
            dropped['outside the US'] += 1
            continue
        found = C.seasons(title, ' '.join(r.get('terms') or []), r.get('description', '')[:3000])
        if found and all(C.season_past(s, today) for s in found):
            dropped['season already over'] += 1
            continue
        flags = C.eligibility(title, r.get('description', ''), r.get('degrees'))
        if (flags.get('citizenship_only') or flags.get('clearance')) and not citizen:
            dropped['US citizenship or clearance required'] += 1
            continue
        if flags['degree_rule'] in ('phd_only', 'mba_only'):
            dropped['PhD- or MBA-only'] += 1
            continue
        lane, stretch = C.lane_of(title)
        emp = C.employer(r['company'])
        if emp.get('tier') == 'enterprise' and emp.get('name'):
            r['company'] = emp['name']            # 'The Walt Disney Company' and 'Disney' are one employer
        r.update(country='US' if where == 'US' else 'unknown', seasons=found, flags=flags, lane=lane,
                 stretch=stretch, industry=C.industry_of(r['company'], r.get('description', ''), title),
                 tier=emp.get('tier') or ('growth' if r.get('origin_kind') == 'board' else ''),
                 usc=bool(emp.get('usc')), korean_company=bool(emp.get('korean')),
                 job_key=state.job_key(r['company'], title))
        if r['korean_company']:
            flags.setdefault('arenas', []).append('korean')
        if r['url']:
            try:
                r['url'] = canonical_url(r['url'])
            except ValueError:
                dropped['bad link'] += 1
                continue
        kept.append(r)
    return kept, dropped


def merge(rows):
    """One row per job (company + title without place names). The employer's own
    posting wins over a list or LinkedIn copy; other cities become also_locations."""
    by_url, groups = {}, {}
    for r in rows:
        if r['url'] in by_url:
            by_url[r['url']]['found_by'].add(r['source'])
            continue
        r['found_by'] = {r['source']}
        by_url[r['url']] = r
        groups.setdefault(r['job_key'], []).append(r)
    out = []
    for key, g in groups.items():
        g.sort(key=lambda x: (SOURCE_RANK.get(x['source'], 9), -len(x.get('description') or ''),
                              -(iso_date(x.get('posted')) or date(2000, 1, 1)).toordinal()))
        rep = dict(g[0])
        rep['found_by'] = sorted(set().union(*(x['found_by'] for x in g)))
        locs = [x['location'] for x in g[1:] if x['location'] and x['location'] != rep['location']]
        rep['also_locations'] = list(dict.fromkeys(locs))[:12]
        rep['also_urls'] = [x['url'] for x in g[1:4]]
        if not rep.get('posted'):
            rep['posted'] = next((x['posted'] for x in g if x.get('posted')), None)
        for x in g[1:]:
            for k in ('degrees', 'terms', 'pay_text', 'deadline'):
                if not rep.get(k) and x.get(k):
                    rep[k] = x[k]
        out.append(rep)
    return out


# ------------------------------------------------------------------ pre-rank
def prescore(r, prefs, today):
    f = r['flags']
    parts = {}
    parts['employer'] = {'enterprise': 25, 'large': 18, 'growth': 8}.get(r['tier'], 10)
    parts['direction'] = (25 if r['lane'] in CORE_LANES else 20 if r['lane'] in ('finance', 'data') else 8) \
        - (8 if r['stretch'] else 0)
    parts['degree'] = {'graduate_allowed': 15, 'unknown': 8, 'related_degree': 10}.get(f['degree_rule'], 0)
    seas = r.get('seasons') or []
    parts['season'] = 10 if any(s in PASSING_SEASONS for s in seas) else 3 if 'Fall 2026' in seas else 6
    posted = iso_date(r.get('posted'))
    age = (today - posted).days if posted else None
    parts['fresh'] = 4 if age is None else 10 if age <= 7 else 7 if age <= 21 else 4 if age <= 60 else 1
    parts['pay_listed'] = 3 if (r.get('pay_text') or f.get('pay_text')) else 0
    parts['usc'] = 4 if r.get('usc') else 0
    ar = f.get('arenas') or []
    parts['her_edge'] = (5 if 'korean' in ar else 0) + (3 if 'beauty_commerce' in ar else 0)
    from ..hub.db import pref_adjust
    parts['her_ratings'] = pref_adjust(prefs, r['lane'], r['industry'])
    if f['degree_rule'] == 'undergraduate_only':
        parts['degree'] = -20
    return round(max(0, min(100, sum(parts.values()))), 1), parts


def select_queue(rows, size):
    """Highest pre-rank first, at most 2 per employer, no direction above 40% of the
    queue, and the last few slots kept for directions/industries not yet present."""
    rows = sorted(rows, key=lambda r: -r['prescore'])
    explore_slots = max(2, size // 8)
    chosen, per_co, per_lane = [], Counter(), Counter()
    for r in rows:
        if len(chosen) >= size - explore_slots:
            break
        co = C.canon_company(r['company'])
        if per_co[co] >= 2 or per_lane[r['lane']] >= max(3, int(0.4 * size)):
            continue
        if r['flags']['degree_rule'] == 'undergraduate_only':
            continue
        chosen.append(r)
        per_co[co] += 1
        per_lane[r['lane']] += 1
    have_l, have_i = {r['lane'] for r in chosen}, {r['industry'] for r in chosen}
    for r in rows:
        if len(chosen) >= size:
            break
        if r in chosen or per_co[C.canon_company(r['company'])] >= 2 or r['flags']['degree_rule'] == 'undergraduate_only':
            continue
        if r['lane'] not in have_l or r['industry'] not in have_i:
            r['exploration'] = True
            chosen.append(r)
            have_l.add(r['lane'])
            have_i.add(r['industry'])
            per_co[C.canon_company(r['company'])] += 1
    for r in rows:   # top up if exploration found too few
        if len(chosen) >= size:
            break
        if r not in chosen and per_co[C.canon_company(r['company'])] < 2 and r['flags']['degree_rule'] != 'undergraduate_only':
            chosen.append(r)
            per_co[C.canon_company(r['company'])] += 1
    return chosen


def enrich(rows, workers=8):
    def one(r):
        if (r.get('description') or '') and len(r['description']) > 400:
            return r
        try:
            desc, extra = sources.fetch_detail(r)
        except Exception as e:  # noqa: BLE001
            r['enrich_error'] = f'{type(e).__name__}: {str(e)[:60]}'
            return r
        if desc:
            r['description'] = desc[:15000]
        for k, v in extra.items():
            if v not in (None, ''):
                r[k] = v
        return r
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, rows))


# ------------------------------------------------------------------ outputs
def _slug(s, n=40):
    return re.sub(r'[^a-z0-9]+', '_', (s or '').lower()).strip('_')[:n]


def _auto_checks(r):
    f, checks = r['flags'], []
    if r['country'] != 'US':
        checks.append('Confirm the job is in the US (or US-remote)')
    if f['degree_rule'] == 'unknown':
        checks.append(f.get('degree_note') or "Confirm a master's student is eligible")
    if f.get('grad_min') or f.get('grad_max'):
        checks.append(f"Graduation window {f.get('grad_min', '?')} – {f.get('grad_max', '?')}: compare with hers")
    if f.get('min_gpa'):
        checks.append(f"Minimum GPA {f['min_gpa']}")
    if 'Fall 2026' in (r.get('seasons') or []):
        checks.append('Fall 2026 term may already have started — confirm start date and hours')
    if r['source'] in ('linkedin', 'simplify') and 'myworkdayjobs' not in r['url'] and 'greenhouse' not in r['url']:
        checks.append('Open the employer posting and use its link (not a list/LinkedIn copy) if one exists')
    return checks


def write_scan(out_dir, queue, rest, summary):
    jd_dir = out_dir / 'jds'
    jd_dir.mkdir(parents=True, exist_ok=True)
    q_out, template = [], []
    for i, r in enumerate(queue, 1):
        jd = jd_dir / f'{i:02d}_{_slug(r["company"], 24)}_{_slug(r["title"], 40)}.txt'
        jd.write_text(f"{r['company']} — {r['title']}\n{r['location']}\n{r['url']}\nPosted: {r.get('posted') or 'unknown'}"
                      f"\nFound by: {', '.join(r['found_by'])}\n\n{r.get('description') or '(no text fetched — open the link)'}\n")
        r['jd_path'] = str(jd.relative_to(paths.ROOT))
        r['idx'] = i
        q_out.append(_public(r))
        template.append({
            'key': r['job_key'], 'idx': i, 'company': r['company'], 'title': r['title'], 'url': r['url'],
            'lane': r['lane'], 'industry': r['industry'], 'country': r['country'],
            'pay': r.get('pay_text') or r['flags'].get('pay_text') or 'Not published',
            'timing': ', '.join(r.get('seasons') or r.get('terms') or []) or 'Not stated',
            'deadline': r.get('deadline'), 'degree_rule': r['flags']['degree_rule'],
            'checks': _auto_checks(r), 'blockers': [],
            'decision': 'TODO recommend | check | skip',
            'why': 'TODO', 'gaps': 'TODO', 'task': 'TODO', 'conversion': 'TODO', 'next_step': 'TODO',
            'evidence_note': 'TODO', 'verification': 'TODO ats_live | employer_live | employer_indexed | secondary | closed',
            'verified_at': date.today().isoformat(), 'technical_level': 'TODO 1-5',
            'score_components': {'transfer': 'TODO /30', 'learning': 'TODO /25', 'readiness': 'TODO /20',
                                 'pay': 'TODO /10', 'conversion': 'TODO /10', 'timing': 'TODO /5'},
            'reviewed': True})
    atomic_json(out_dir / 'queue.json', q_out)
    atomic_json(out_dir / 'rest.json', [_public(r) for r in rest])
    atomic_json(out_dir / 'review_template.json', template)
    atomic_json(out_dir / 'summary.json', summary)


def _public(r):
    keep = ('job_key', 'idx', 'company', 'title', 'location', 'also_locations', 'url', 'also_urls', 'source',
            'found_by', 'board', 'posted', 'deadline', 'lane', 'stretch', 'industry', 'tier', 'usc', 'seasons',
            'terms', 'degrees', 'flags', 'country', 'prescore', 'prescore_parts', 'jd_path', 'exploration',
            'pay_text', 'start_date', 'can_apply', 'enrich_error', 'query')
    return {k: r[k] for k in keep if k in r and r[k] not in (None, '', [], {})}


def scan(queue_size=40, general_boards=True, use_linkedin=True, workers=16, log=print):
    from ..hub import db
    today = date.today()
    base = paths.OUTPUT / 'scans' / ('scan-' + today.isoformat())
    out_dir, n = base, 2
    while out_dir.exists():
        out_dir = base.with_name(base.name + f'-{n}')
        n += 1
    scan_id = out_dir.name
    rows, health = harvest(general_boards, use_linkedin, workers, log, today)
    never = db.never_set() | {C.canon_company(x) for x in _load('exclusions.json').get('never', [])}
    citizen = bool(_lead_profile().get('work_authorization', {}).get('us_citizen'))
    kept, dropped = classify_rows(rows, today, never, citizen)
    merged = merge(kept)
    seen = state.seen()['keys']
    suppressed = db.suppressed_keys()
    fresh, skip = [], Counter()
    for r in merged:
        if r['job_key'] in suppressed:
            skip['she already acted on it'] += 1
        elif state.recently_seen(seen, r['job_key'], today):
            skip[f'shown in the last {state.RESURFACE_DAYS} days'] += 1
        else:
            fresh.append(r)
    prefs = db.preferences()
    for r in fresh:
        r['prescore'], r['prescore_parts'] = prescore(r, prefs, today)
    pick = select_queue(fresh, int(queue_size * 1.4))
    _say(log, f'[scan] reading full postings for {len(pick)} queue candidates')
    pick = enrich(pick)
    survivors, gone = [], set()
    for r in pick:
        r['flags'] = {**C.eligibility(r['title'], r.get('description', ''), r.get('degrees')),
                      **({'arenas': r['flags']['arenas']} if r['flags'].get('arenas') else {})}
        if ((r['flags'].get('citizenship_only') or r['flags'].get('clearance')) and not citizen) or \
                r['flags']['degree_rule'] in ('phd_only', 'mba_only'):
            dropped['US citizenship / clearance / PhD / MBA (found in full text)'] += 1
            gone.add(r['job_key'])
            continue
        r['seasons'] = C.seasons(r['title'], ' '.join(r.get('terms') or []), (r.get('description') or '')[:4000])
        if r['seasons'] and all(C.season_past(s, today) for s in r['seasons']):
            dropped['season already over (full text)'] += 1
            gone.add(r['job_key'])
            continue
        r['prescore'], r['prescore_parts'] = prescore(r, prefs, today)
        survivors.append(r)
    queue = select_queue(survivors, queue_size)
    qkeys = {r['job_key'] for r in queue}
    rest = sorted((r for r in fresh if r['job_key'] not in qkeys and r['job_key'] not in gone),
                  key=lambda r: -r['prescore'])
    warnings = []
    for lane, h in health.items():
        if lane == 'linkedin':
            if h.get('status') in ('blocked', 'failed'):
                warnings.append(f"LinkedIn {h['status']}: {h.get('note')}")
        elif h.get('sources') and h.get('ok', 0) < 0.5 * h['sources']:
            warnings.append(f"{lane}: only {h['ok']} of {h['sources']} sources answered")
        h['kept'] = sum(1 for r in merged if _lane_of_row(r) == lane)
        w = state.collapse_warning(lane, h['kept'], scan_id)
        if w:
            warnings.append(w)
    state.save_health(scan_id, {k: {'kept': v.get('kept', 0)} for k, v in health.items()})
    summary = {'scan_id': scan_id, 'day': today.isoformat(), 'raw_rows': len(rows),
               'internships_in_scope': len(kept), 'unique_jobs': len(merged), 'new_to_her': len(fresh),
               'queued_for_review': len(queue), 'listed_unreviewed': len(rest),
               'dropped': dict(dropped.most_common()), 'skipped': dict(skip),
               'by_lane': dict(Counter(r['lane'] for r in fresh)), 'by_industry': dict(Counter(r['industry'] for r in fresh)),
               'by_tier': dict(Counter(r['tier'] or 'unknown' for r in fresh)),
               'by_source': dict(Counter(s for r in merged for s in r['found_by'])),
               'health': health, 'warnings': warnings}
    write_scan(out_dir, queue, rest, summary)
    _say(log, f'[scan] {len(rows)} postings read → {len(kept)} US internships in scope → {len(merged)} unique → '
              f'{len(fresh)} new to her → {len(queue)} queued for review ({out_dir.relative_to(paths.ROOT)})')
    for w in warnings:
        _say(log, '[scan] WARNING ' + w)
    return out_dir


def _lane_of_row(r):
    if r['source'] == 'linkedin':
        return 'linkedin'
    if r['source'] == 'simplify':
        return 'simplify'
    if r['source'] == 'bigtech' or r.get('board', '').split(':')[0] in ('amazon', 'microsoft', 'apple', 'google'):
        return 'bigtech'
    return 'enterprise' if r.get('origin_kind') == 'enterprise' else 'boards'


# ------------------------------------------------------------------ mark
REVIEW_TEXT = ('company', 'title', 'url', 'lane', 'country', 'pay', 'timing', 'why', 'gaps', 'task', 'conversion',
               'evidence_note', 'next_step')
CAPS = {'transfer': 30, 'learning': 25, 'readiness': 20, 'pay': 10, 'conversion': 10, 'timing': 5}


def validate_review(j, lane_ids):
    """Same bar as engine.import_reviewed, plus a decision. Raises ValueError."""
    if not isinstance(j, dict) or j.get('reviewed') is not True:
        raise ValueError('every review needs reviewed=true after reading the full posting')
    name = f"{j.get('company')} — {j.get('title')}"
    if j.get('decision') not in ('recommend', 'check', 'skip'):
        raise ValueError(f'{name}: decision must be recommend, check or skip')
    for k in REVIEW_TEXT:
        if not isinstance(j.get(k), str) or not j[k].strip() or j[k].startswith('TODO'):
            raise ValueError(f'{name}: missing {k}')
    canonical_url(j['url'])
    if j['lane'] not in lane_ids:
        raise ValueError(f'{name}: unknown lane {j["lane"]}')
    if j.get('verification') not in ('ats_live', 'employer_live', 'employer_indexed', 'secondary', 'closed'):
        raise ValueError(f'{name}: state how the posting was verified')
    v = iso_date(j.get('verified_at'))
    if not v or v > date.today():
        raise ValueError(f'{name}: a real, non-future verified_at date is required')
    if not isinstance(j.get('technical_level'), int) or not 1 <= j['technical_level'] <= 5:
        raise ValueError(f'{name}: technical_level must be 1-5')
    sc = j.get('score_components')
    if not isinstance(sc, dict):
        raise ValueError(f'{name}: score_components required')
    for k, cap in CAPS.items():
        if not isinstance(sc.get(k), (int, float)) or not 0 <= sc[k] <= cap:
            raise ValueError(f'{name}: score {k} must be 0-{cap}')
    for k in ('checks', 'blockers'):
        if not isinstance(j.get(k), list) or any(not isinstance(x, str) for x in j[k]):
            raise ValueError(f'{name}: {k} must be a list of text')
    if j.get('degree_rule') not in ('graduate_allowed', 'related_degree', 'unknown', 'undergraduate_only',
                                    'phd_only', 'mba_only'):
        raise ValueError(f'{name}: degree_rule required')
    if j['degree_rule'] == 'unknown' and not j['checks']:
        raise ValueError(f'{name}: unknown degree eligibility needs a check')
    if j['decision'] == 'skip' and not (j['blockers'] or j['checks'] or j.get('skip_reason')):
        raise ValueError(f'{name}: a skip needs a blocker, check or skip_reason')


def mark(scan_dir, reviewed_path=None, log=print, force=False):
    """force=True re-applies reviews already marked (after a review or profile fact changed)."""
    from ..engine import assess
    from ..hub import db
    from pathlib import Path
    scan_dir = Path(scan_dir)
    if not scan_dir.is_absolute():
        scan_dir = paths.ROOT / scan_dir
    queue = json.loads((scan_dir / 'queue.json').read_text())
    rest = json.loads((scan_dir / 'rest.json').read_text())
    summary = json.loads((scan_dir / 'summary.json').read_text())
    rp = Path(reviewed_path) if reviewed_path else scan_dir / 'reviewed.json'
    reviews = json.loads(rp.read_text()) if rp.exists() else []
    if not isinstance(reviews, list):
        raise ValueError('reviewed.json must be a list')
    lane_ids = {l['id'] for l in _load('lanes.json')['lanes']}
    errors = []
    for j in reviews:
        try:
            validate_review(j, lane_ids)
        except ValueError as e:
            errors.append(str(e))
    if errors:
        raise ValueError('Fix these reviews first:\n  ' + '\n  '.join(errors))
    profile = _lead_profile()
    by_key = {q['job_key']: q for q in queue + rest}
    day = summary.get('day') or date.today().isoformat()   # the scan's LOCAL day, even if marked later
    out = []
    reviewed_keys = set()
    for j in reviews:
        base = by_key.get(j.get('key')) or {}
        key = j.get('key') or state.job_key(j['company'], j['title'])
        job = dict(base)
        job.update({k: v for k, v in j.items() if k not in ('key',)})
        job['job_key'] = key
        fit = round(sum(float(j['score_components'][k]) for k in CAPS), 1)
        blockers, checks = (assess(dict(job, reviewed=True), profile) if profile else (j['blockers'], j['checks']))
        if j['decision'] == 'skip' or blockers:
            bucket = 'excluded'
        elif j['decision'] == 'check' or checks:
            bucket = 'check'
        elif job.get('technical_level', 0) >= 4 or job.get('stretch'):
            bucket = 'stretch'
        else:
            bucket = 'apply'
        review = {k: j.get(k) for k in ('why', 'gaps', 'task', 'conversion', 'next_step', 'evidence_note', 'verification',
                                        'verified_at', 'technical_level', 'score_components', 'checks', 'degree_rule',
                                        'decision', 'skip_reason', 'grad_min', 'grad_max', 'min_gpa')}
        review['checks'], review['blockers'] = checks, blockers
        out.append(dict(job_key=key, company=j['company'], title=j['title'], url=j['url'],
                        location=base.get('location') or j.get('location', ''), also_locations=base.get('also_locations'),
                        lane=j['lane'], stretch=base.get('stretch'), industry=j.get('industry') or base.get('industry'),
                        tier=base.get('tier') or C.employer(j['company']).get('tier') or '', source=base.get('source', 'review'),
                        board=base.get('board'), season=j.get('timing'), posted=base.get('posted'), pay=j['pay'],
                        deadline=j.get('deadline') or base.get('deadline'), arenas=(base.get('flags') or {}).get('arenas'),
                        jd_path=base.get('jd_path'), prescore=base.get('prescore'), prescore_parts=base.get('prescore_parts'),
                        reviewed=True, fit=fit, review=review, bucket=bucket))
        reviewed_keys.add(key)
    for r in queue + rest:
        if r['job_key'] in reviewed_keys:
            continue
        out.append(dict(job_key=r['job_key'], company=r['company'], title=r['title'], url=r['url'],
                        location=r.get('location'), also_locations=r.get('also_locations'), lane=r['lane'],
                        stretch=r.get('stretch'), industry=r.get('industry'), tier=r.get('tier', ''),
                        source=r.get('source'), board=r.get('board'), season=', '.join(r.get('seasons') or []),
                        posted=r.get('posted'), pay=r.get('pay_text') or (r.get('flags') or {}).get('pay_text'),
                        deadline=r.get('deadline'), arenas=(r.get('flags') or {}).get('arenas'), jd_path=r.get('jd_path'),
                        prescore=r.get('prescore'), prescore_parts=r.get('prescore_parts'), reviewed=False,
                        bucket='unreviewed', review={'flags': r.get('flags'), 'checks': _auto_checks_public(r)}))
    counts = db.upsert_surfaced(out, day, summary['scan_id'], force=force)
    state.mark_seen([(k, summary['scan_id']) for k in reviewed_keys | {q['job_key'] for q in queue}], day)
    meta = {'scan_id': summary['scan_id'], 'reviewed': len(reviews),
            'recommended': sum(1 for j in reviews if j['decision'] == 'recommend'),
            'listed_unreviewed': len(out) - len(reviews), 'warnings': summary.get('warnings', []),
            'funnel': {k: summary[k] for k in ('raw_rows', 'internships_in_scope', 'unique_jobs', 'new_to_her',
                                               'queued_for_review')},
            'dropped': summary.get('dropped'), 'by_source': summary.get('by_source')}
    db.save_day(day, meta)
    log(f"[mark] hub updated for {day}: {len(reviews)} reviewed ({meta['recommended']} recommended), "
        f"{meta['listed_unreviewed']} listed as score unknown; {counts}")
    return meta


def _auto_checks_public(r):
    try:
        return _auto_checks(dict(r, flags=r.get('flags') or {'degree_rule': 'unknown'}, country=r.get('country', 'unknown')))
    except Exception:  # noqa: BLE001
        return []


if __name__ == '__main__':
    scan(log=lambda m: print(m, file=sys.stderr))
