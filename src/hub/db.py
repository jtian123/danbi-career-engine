"""Danbi's Career Hub database — data/hub/hub.db (SQLite, private, never tracked).

* One row per job. `job_key` (company + title without place names) is shared with
  the scanner's memory, so the two join for free.
* Every status change, rating and surfacing also appends to `events`: history is
  never lost, and the calendar can show what happened on each day.
* Danbi sets statuses and ratings herself in the hub (no inbox sync on her side).
* Scores are an ordering aid, never an acceptance probability. Big employers get a
  visible priority bonus (her stated preference: try large companies first).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from .. import paths
from ..scan.classify import canon_company

STATUSES = ('new', 'saved', 'applied', 'interviewing', 'offer', 'rejected', 'withdrawn', 'closed', 'dismissed')
TRACKED = ('applied', 'interviewing', 'offer', 'rejected', 'withdrawn')
INTEREST = {'interested': 1.0, 'curious': 0.4, 'not_for_me': -1.0}
REASONS = ('', 'work', 'industry', 'technical', 'pay', 'location', 'timing', 'other')
CONFIDENCE = ('', 'ready', 'learn', 'too_technical')
TIER_BONUS = {'enterprise': 8, 'large': 5, 'university': 5}
PREF_CAP = 10.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY,
  job_key TEXT UNIQUE NOT NULL,
  company TEXT, company_canon TEXT, title TEXT, url TEXT, location TEXT, also_locations TEXT,
  lane TEXT, stretch INTEGER DEFAULT 0, industry TEXT, tier TEXT, source TEXT, board TEXT,
  season TEXT, posted TEXT, pay TEXT, deadline TEXT, arenas TEXT, jd_path TEXT,
  first_surfaced TEXT, last_surfaced TEXT, scan_id TEXT, origin TEXT DEFAULT 'scan',
  prescore REAL, prescore_parts TEXT,
  reviewed INTEGER DEFAULT 0, fit REAL, review TEXT, bucket TEXT,
  status TEXT NOT NULL DEFAULT 'new', status_date TEXT,
  interest TEXT DEFAULT '', reason TEXT DEFAULT '', confidence TEXT DEFAULT '', notes TEXT DEFAULT '',
  rated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_day ON jobs(last_surfaced);
CREATE INDEX IF NOT EXISTS idx_jobs_canon ON jobs(company_canon);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, job_id INTEGER, kind TEXT NOT NULL, day TEXT NOT NULL, note TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id);
CREATE TABLE IF NOT EXISTS campus_events(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, date TEXT NOT NULL, end_date TEXT, time TEXT,
  url TEXT, kind TEXT, description TEXT, source TEXT DEFAULT 'registry', verified INTEGER DEFAULT 0,
  done INTEGER DEFAULT 0, UNIQUE(title, date)
);
CREATE TABLE IF NOT EXISTS days(day TEXT PRIMARY KEY, meta TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS never(company_canon TEXT PRIMARY KEY, company TEXT, added TEXT);
"""


def today() -> str:
    return date.today().isoformat()      # LOCAL day: an evening action belongs to tonight


def connect() -> sqlite3.Connection:
    p = paths.data('hub')
    p.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p / 'hub.db'), timeout=15)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    _migrate(con)
    return con


def _migrate(con):
    have = {r[1] for r in con.execute('PRAGMA table_info(jobs)')}
    added = False
    for name, typ in _NEW_COLUMNS:
        if name not in have:
            con.execute(f'ALTER TABLE jobs ADD COLUMN {name} {typ}')
            added = True
    if added:
        con.execute("UPDATE jobs SET track='internship' WHERE track IS NULL")
        con.commit()


def _event(con, job_id, kind, day, note=''):
    con.execute('INSERT INTO events(job_id, kind, day, note) VALUES(?,?,?,?)', (job_id, kind, day, note))


# ------------------------------------------------------------------ ingest from a scan
_JOB_FIELDS = ('company', 'title', 'url', 'location', 'also_locations', 'lane', 'stretch', 'industry', 'tier',
               'source', 'board', 'season', 'posted', 'pay', 'deadline', 'arenas', 'jd_path', 'prescore',
               'prescore_parts', 'reviewed', 'fit', 'review', 'bucket', 'track')
# Columns added after the first release. Existing hub.db files get them through _migrate();
# _SCHEMA stays the original table so old databases keep opening. `track` is the job kind:
# 'internship' or 'staff' (a full-time salaried staff role, e.g. at a university). The other
# columns are unused leftovers of an abandoned design, kept only so old files still open.
_NEW_COLUMNS = (('track', "TEXT DEFAULT 'internship'"), ('employment', 'TEXT'), ('uni_function', 'TEXT'),
                ('university', 'TEXT'), ('pay_min', 'REAL'), ('pay_max', 'REAL'), ('pay_unit', 'TEXT'),
                ('pay_annual_max', 'REAL'))


def _pack(j: dict) -> dict:
    f = {k: j.get(k) for k in _JOB_FIELDS}
    f['track'] = f['track'] or 'internship'
    for k in ('also_locations', 'arenas', 'prescore_parts', 'review'):
        if f[k] is not None and not isinstance(f[k], str):
            f[k] = json.dumps(f[k], ensure_ascii=False)
    f['stretch'] = int(bool(f['stretch']))
    f['reviewed'] = int(bool(f['reviewed']))
    f['company_canon'] = canon_company(j.get('company'))
    return f


def upsert_surfaced(rows, day: str, scan_id: str, origin: str = 'scan', force: bool = False) -> dict:
    """Fold one scan's rows into the hub. Idempotent per scan. A job she already
    acted on keeps its status and her ratings; only surfacing facts refresh, and a
    reviewed score is never replaced by an unreviewed listing."""
    con = connect()
    n = {'new': 0, 'refreshed': 0, 'kept_status': 0}
    try:
        for j in rows:
            f = _pack(j)
            row = con.execute('SELECT id, status, reviewed, scan_id FROM jobs WHERE job_key=?', (j['job_key'],)).fetchone()
            relisted = False
            if row:
                if row['scan_id'] == scan_id and row['reviewed'] >= f['reviewed'] and not force:
                    continue
                relisted = not f['reviewed']
                if row['reviewed'] and relisted:
                    f = {k: f[k] for k in ('prescore', 'prescore_parts', 'also_locations')}
                if row['status'] not in ('new', 'dismissed'):
                    f = {k: v for k, v in f.items() if k not in ('url', 'title', 'company')}
                    n['kept_status'] += 1
                f['scan_id'] = scan_id
                if not relisted:          # a re-found listing never moves a job to a new day;
                    f['last_surfaced'] = day   # a fresh REVIEW does (it is news for that day)
                con.execute('UPDATE jobs SET ' + ','.join(f'{k}=?' for k in f) + ' WHERE id=?', (*f.values(), row['id']))
                job_id = row['id']
                n['refreshed'] += 1
            else:
                f.update(job_key=j['job_key'], first_surfaced=day, last_surfaced=day, scan_id=scan_id, origin=origin)
                cur = con.execute('INSERT INTO jobs(' + ','.join(f) + ') VALUES(' + ','.join('?' * len(f)) + ')',
                                  tuple(f.values()))
                job_id = cur.lastrowid
                n['new'] += 1
            if not (row and relisted):
                _event(con, job_id, 'surfaced' if f.get('reviewed') else 'listed', day, scan_id)
        con.commit()
    finally:
        con.close()
    return n


def save_day(day: str, meta: dict):
    con = connect()
    try:
        old = con.execute('SELECT meta FROM days WHERE day=?', (day,)).fetchone()
        merged = json.loads(old['meta']) if old else {}
        merged.update(meta)
        con.execute('INSERT INTO days(day, meta) VALUES(?,?) ON CONFLICT(day) DO UPDATE SET meta=excluded.meta',
                    (day, json.dumps(merged, ensure_ascii=False)))
        con.commit()
    finally:
        con.close()


# ------------------------------------------------------------------ Danbi's actions
def set_status(job_id: int, status: str, note: str = '') -> dict:
    if status not in STATUSES:
        raise ValueError('unknown status ' + repr(status))
    con = connect()
    try:
        d = today()
        con.execute('UPDATE jobs SET status=?, status_date=? WHERE id=?', (status, d, job_id))
        _event(con, job_id, status, d, note)
        con.commit()
        return dict(con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
    finally:
        con.close()


def rate(job_id: int, interest='', reason='', confidence='', notes=None) -> dict:
    if interest not in ('',) + tuple(INTEREST) or reason not in REASONS or confidence not in CONFIDENCE:
        raise ValueError('invalid rating')
    con = connect()
    try:
        cur = con.execute('SELECT notes FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not cur:
            raise KeyError(job_id)
        notes = cur['notes'] if notes is None else str(notes)[:4000]
        con.execute('UPDATE jobs SET interest=?, reason=?, confidence=?, notes=?, rated_at=? WHERE id=?',
                    (interest, reason, confidence, notes, datetime.now().isoformat(timespec='seconds'), job_id))
        _event(con, job_id, 'rated', today(), f'{interest} {reason} {confidence}'.strip())
        con.commit()
        return dict(con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
    finally:
        con.close()


def add_lead(company, title, url='', source='handshake', location='', notes='', deadline='', lane='explore',
             industry='') -> dict:
    """A job she found herself (Handshake, a fair, a referral, an email)."""
    from ..scan.classify import industry_of, lane_of, employer
    from ..scan.state import job_key
    if not (company or '').strip() or not (title or '').strip():
        raise ValueError('company and title are required')
    if url and not url.startswith(('http://', 'https://')):
        raise ValueError('link must start with http(s)://')
    if deadline:
        date.fromisoformat(deadline)
    lane = lane if lane and lane != 'explore' else lane_of(title)[0]
    tier = employer(company).get('tier') or ''
    row = dict(job_key=job_key(company, title), company=company.strip(), title=title.strip(), url=url.strip(),
               location=location, lane=lane, industry=industry or industry_of(company), tier=tier,
               source=source, board=source, deadline=deadline or None, reviewed=0)
    upsert_surfaced([row], today(), 'lead-' + today(), origin='lead')
    con = connect()
    try:
        r = con.execute('SELECT id FROM jobs WHERE job_key=?', (row['job_key'],)).fetchone()
        if notes:
            con.execute('UPDATE jobs SET notes=? WHERE id=?', (notes[:4000], r['id']))
        con.execute("UPDATE jobs SET status='saved', status_date=? WHERE id=? AND status='new'", (today(), r['id']))
        con.commit()
        return dict(con.execute('SELECT * FROM jobs WHERE id=?', (r['id'],)).fetchone())
    finally:
        con.close()


def never_add(company: str) -> bool:
    con = connect()
    try:
        cur = con.execute('INSERT OR IGNORE INTO never(company_canon, company, added) VALUES(?,?,?)',
                          (canon_company(company), company, today()))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def never_set() -> set:
    con = connect()
    try:
        return {r['company_canon'] for r in con.execute('SELECT company_canon FROM never')}
    finally:
        con.close()


# ------------------------------------------------------------------ campus calendar
def sync_campus(campus: dict) -> int:
    """Load dated USC events from registry/campus.json (idempotent)."""
    con = connect()
    n = 0
    try:
        for e in campus.get('events', []):
            try:
                date.fromisoformat(e['date'])
            except (KeyError, ValueError):
                continue
            cur = con.execute('INSERT OR IGNORE INTO campus_events(title, date, end_date, time, url, kind, description,'
                              ' source, verified) VALUES(?,?,?,?,?,?,?,?,?)',
                              (e['title'], e['date'], e.get('end_date'), e.get('time'), e.get('url'), e.get('kind'),
                               e.get('description'), 'registry', int(bool(e.get('verified')))))
            n += cur.rowcount
        con.commit()
    finally:
        con.close()
    return n


def add_campus_event(title, day, url='', kind='event', description='', time='', end_date=None) -> int:
    date.fromisoformat(day)
    if not (title or '').strip():
        raise ValueError('title required')
    con = connect()
    try:
        cur = con.execute('INSERT OR IGNORE INTO campus_events(title, date, end_date, time, url, kind, description,'
                          ' source, verified) VALUES(?,?,?,?,?,?,?,?,1)',
                          (title.strip(), day, end_date, time, url, kind, description, 'danbi'))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def mark_campus_done(event_id: int, done: bool = True):
    con = connect()
    try:
        con.execute('UPDATE campus_events SET done=? WHERE id=?', (int(done), event_id))
        con.commit()
    finally:
        con.close()


# ------------------------------------------------------------------ learning from her ratings
def preferences(con=None) -> dict:
    """Bounded lane and industry adjustments from her ratings.
    adj = 10 * sum(votes) / (3 + n): sparse evidence is shrunk toward 0, capped at ±10.
    'Not for me' counts against a direction only when the reason is the work itself,
    and against an industry only when the reason is the industry — a pay, timing or
    location dislike says nothing about the career."""
    own = con is None
    con = con or connect()
    try:
        lane, ind = {}, {}
        for r in con.execute("SELECT lane, industry, interest, reason FROM jobs WHERE interest != ''"):
            v = INTEREST.get(r['interest'])
            if v is None:
                continue
            if v > 0 or r['reason'] == 'work':
                lane.setdefault(r['lane'] or 'explore', []).append(v)
            if v > 0 or r['reason'] == 'industry':
                ind.setdefault(r['industry'] or 'other', []).append(v)

        def adj(votes):
            return {k: {'adjust': round(max(-PREF_CAP, min(PREF_CAP, 10 * sum(v) / (3 + len(v)))), 1), 'votes': len(v)}
                    for k, v in votes.items()}
        return {'lane': adj(lane), 'industry': adj(ind)}
    finally:
        if own:
            con.close()


def pref_adjust(prefs: dict, lane: str, industry: str) -> float:
    a = prefs['lane'].get(lane or 'explore', {}).get('adjust', 0) + prefs['industry'].get(industry or 'other', {}).get('adjust', 0)
    return max(-PREF_CAP, min(PREF_CAP, a))


def suppressed_keys() -> set:
    """Jobs the scanner must never queue again: she acted on them or said not for me."""
    con = connect()
    try:
        return {r['job_key'] for r in con.execute(
            "SELECT job_key FROM jobs WHERE status NOT IN ('new','saved') OR interest='not_for_me'")}
    finally:
        con.close()


def applied_companies(days: int = 120) -> dict:
    """canon company -> last application day (shown as a chip, never a filter)."""
    con = connect()
    try:
        out = {}
        for r in con.execute("SELECT company_canon, max(status_date) d FROM jobs WHERE status IN "
                             "('applied','interviewing','offer','rejected') GROUP BY company_canon"):
            out[r['company_canon']] = r['d']
        return out
    finally:
        con.close()


# ------------------------------------------------------------------ dashboard state
def full_state() -> dict:
    con = connect()
    try:
        jobs = []
        for r in con.execute('SELECT * FROM jobs'):
            j = dict(r)
            for k in ('also_locations', 'arenas', 'prescore_parts', 'review'):
                try:
                    j[k] = json.loads(j[k]) if j[k] else None
                except ValueError:
                    pass
            jobs.append(j)
        prefs = preferences(con)
        for j in jobs:
            j['pref'] = pref_adjust(prefs, j['lane'], j['industry'])
            j['tier_bonus'] = TIER_BONUS.get(j['tier'] or '', 0)
            base = j['fit'] if j['reviewed'] and j['fit'] is not None else None
            j['priority'] = round(base + j['tier_bonus'] + j['pref'], 1) if base is not None else None
        events = [dict(r) for r in con.execute('SELECT * FROM events ORDER BY id DESC LIMIT 4000')]
        campus = [dict(r) for r in con.execute('SELECT * FROM campus_events ORDER BY date')]
        days = {r['day']: json.loads(r['meta']) for r in con.execute('SELECT * FROM days')}
        never = [dict(r) for r in con.execute('SELECT * FROM never ORDER BY added DESC')]
        return {'jobs': jobs, 'events': events, 'campus_events': campus, 'days': days, 'prefs': prefs,
                'never': never, 'today': today(), 'tier_bonus': TIER_BONUS}
    finally:
        con.close()
