"""Scanner memory under data/state/ (private, never tracked).

  seen.json          jobs already put in front of Danbi (marked) — not re-queued for 45 days
  boards.json        per-board health: fail streak, last ok; dormant after 4 straight fails
  linkedin.json      query rotation offset + block log (a block skips LinkedIn for the day)
  query_yield.json   distinct companies each LinkedIn query has found (ranks the rotation)
  source_health.json per-scan source counts, for the hub's day panel warnings

Writes are atomic. A corrupt file is quarantined and the run stops — memory that
silently resets would re-show every old job as new.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, timedelta

from .. import paths
from .classify import canon_company, norm_title, us_status

RESURFACE_DAYS = 45
DORMANT_AFTER = 4


def _load(name, default):
    p = paths.state(name)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except ValueError:
        bad = p.with_suffix('.corrupt-' + date.today().isoformat())
        os.replace(p, bad)
        raise RuntimeError(f'{p} was corrupt; moved to {bad}. Inspect it before scanning again.')


def _save(name, obj):
    p = paths.state(name)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix='.' + p.name)
    with os.fdopen(fd, 'w') as f:
        json.dump(obj, f, indent=1, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, p)


# ------------------------------------------------------------------ identity
_CITY_ST = re.compile(r"(?:\b[A-Z][\w.'’]*\.?\s*){1,4},\s*[A-Z]{2}\b(?:\s*\d{5})?")
_SEASON_PHRASE = re.compile(r'(?:starting|start|beginning|for)?\s*(?:summer|spring|fall|autumn|winter)[\s,]*(?:20)?2\d\b',
                            re.I)


_ROLE_WORDS = {'intern', 'internship', 'analyst', 'manager', 'associate', 'executive', 'leadership', 'specialist',
               'coordinator', 'engineer', 'engineering', 'marketing', 'data', 'store', 'operations', 'program',
               'business', 'product', 'finance', 'strategy', 'sales', 'supply', 'chain', 'planning', 'research',
               'insights', 'analytics', 'development', 'management', 'student', 'co-op', 'assistant', 'team'}


def _strip_city(m):
    words = m.group(0).split()
    keep = []
    for w in words:
        if w.strip(",.'’").lower() in _ROLE_WORDS:
            keep.append(w)
        else:
            break
    return ' ' + ' '.join(keep) + ' '


def core_title(title: str) -> str:
    """Title without places and seasons: 'Store Intern - Miami, FL (Starting Summer 2027)' -> 'store intern'.
    Multi-city copies of one program then collapse into one job."""
    from .classify import INTERN
    t = re.sub(r'\([^)]*\)?', ' ', title or '')
    t = _SEASON_PHRASE.sub(' ', re.sub('[\u200b\u00a0]', ' ', t))
    segs = re.split(r'\s+[-–—|:]\s*|\s*[-–—|]\s+', t)
    keep = [x for x in segs if x.strip() and not (us_status(x) == 'US' and not INTERN.search(x))
            and not re.match(r'^\s*(remote|hybrid|onsite|on-site|multiple locations|\d+ locations|greater|central)\b',
                             x, re.I)]
    t = ' '.join(keep or segs)
    t = _CITY_ST.sub(_strip_city, t)
    return norm_title(t)


def job_key(company: str, title: str) -> str:
    return canon_company(company) + '|' + core_title(title)


# ------------------------------------------------------------------ seen
def seen():
    return _load('seen.json', {'version': 1, 'keys': {}})


def recently_seen(keys: dict, key: str, today: date) -> bool:
    rec = keys.get(key)
    if not rec:
        return False
    try:
        return (today - date.fromisoformat(rec['last'])).days < RESURFACE_DAYS
    except (KeyError, ValueError):
        return True


def mark_seen(keys_with_scan, day: str):
    s = seen()
    for key, scan_id in keys_with_scan:
        rec = s['keys'].setdefault(key, {'first': day})
        rec.update(last=day, scan=scan_id)
    _save('seen.json', s)


# ------------------------------------------------------------------ boards
def boards():
    return _load('boards.json', {})


def board_active(rec: dict, today: date) -> bool:
    streak = rec.get('fail_streak', 0)
    if streak < DORMANT_AFTER:
        return True
    try:  # probation: retry a dormant board every 7, 14 … (max 30) days
        last = date.fromisoformat(rec.get('last_fail'))
    except (TypeError, ValueError):
        return True
    return (today - last).days >= min(30, 7 * (streak - DORMANT_AFTER + 1))


def record_boards(results: dict, day: str):
    b = boards()
    for board, ok in results.items():
        rec = b.setdefault(board, {})
        if ok:
            rec.update(fail_streak=0, last_ok=day)
        else:
            rec.update(fail_streak=rec.get('fail_streak', 0) + 1, last_fail=day)
    _save('boards.json', b)


# ------------------------------------------------------------------ LinkedIn
def linkedin():
    return _load('linkedin.json', {'offset': 0, 'blocked_on': None, 'blocks': []})


def save_linkedin(obj):
    _save('linkedin.json', obj)


def query_yield():
    return _load('query_yield.json', {})


def add_query_yield(by_query: dict, day: str):
    y = query_yield()
    for q, companies in by_query.items():
        rec = y.setdefault(q, {'companies': [], 'runs': 0, 'last': None})
        if rec.get('last') != day:
            rec['runs'] += 1
        rec['last'] = day
        rec['companies'] = sorted(set(rec['companies']) | {canon_company(c) for c in companies})
    _save('query_yield.json', y)


def ranked_queries(base: list) -> list:
    """Seed queries ordered by distinct companies found (untried ones first, so new
    phrasings get a turn), then by seed order."""
    y = query_yield()
    order = {q: i for i, q in enumerate(base)}
    return sorted(base, key=lambda q: (q in y, -len(y.get(q, {}).get('companies', [])), order[q]))


# ------------------------------------------------------------------ source health
def save_health(scan_id: str, health: dict):
    h = _load('source_health.json', {})
    h[scan_id] = health
    for k in sorted(h)[:-30]:
        del h[k]
    _save('source_health.json', h)


def health_history():
    return _load('source_health.json', {})


def collapse_warning(source: str, kept: int, scan_id: str):
    """Yield collapse: under 10% of the median of the last 5 scans (median ≥ 20)."""
    prior = [v.get(source, {}).get('kept', 0) for k, v in sorted(health_history().items()) if k != scan_id][-5:]
    if len(prior) < 3:
        return None
    med = sorted(prior)[len(prior) // 2]
    if med >= 20 and kept < 0.1 * med:
        return f'{source} found {kept} vs a recent median of {med} — the source may have changed'
    return None


def since(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()
