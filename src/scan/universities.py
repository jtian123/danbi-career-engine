"""University jobs track: staff and student positions at universities (registry/universities.json).

Not internships, so they never enter the daily internship review queue. They go to the hub's
Universities tab, ranked by pay first and then by fit with her background. Two kinds:
  student — student worker / assistant / graduate assistant jobs she can hold while enrolled
  staff   — full-time university staff roles (watched for pay and fit)

Faculty, clinical/medical-center, trades, IT engineering and senior leadership roles are dropped
(counted). Pay comes from the posting text (most universities publish ranges); details are cached
in data/state/uni_details.json so a posting's page is fetched once.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from .. import paths
from . import net, sources
from .classify import canon_company, lane_of, text, us_status

QUERIES = ['analyst', 'marketing', 'coordinator', 'student', 'admissions', 'program', 'communications',
           'data', 'international', 'advisor']

FUNCTIONS = [  # (label, title pattern) — first match wins; unmatched titles are dropped (counted)
    ('Research administration', r'research (administrat|coordinator|program|development)|sponsored (projects|research)|grants? (administrat|manager|officer|specialist)'),
    ('Data & analytics', r'\bdata\b|analytics?|analyst|institutional research|reporting|business intelligence|assessment|evaluation|insights'),
    ('Marketing & communications', r'marketing|communications?|social media|content|digital|\bweb\b|brand|creative|public relations|media relations|editor|writer'),
    ('Admissions & enrollment', r'admission|enrollment|recruit(ment|er|ing)|outreach|financial aid'),
    ('Career services', r'career'),
    ('International programs', r'international|global|study abroad|korea|asia'),
    ('Student services & advising', r'advis(or|ing|er)|student (affairs|services|success|life|engagement|experience|ambassador)|counselor|residential|orientation'),
    ('Alumni & advancement', r'alumni|advancement|development (officer|associate|coordinator)|donor|fundrais|giving|stewardship'),
    ('Finance & budget', r'financ|budget|accountant|accounting|fiscal|procurement|purchasing|payroll'),
    ('Strategy & planning', r'strateg|planning|policy|chief of staff|special assistant'),
    ('Product & digital learning', r'product (manager|owner)|\bux\b|user experience|instructional design|learning design|digital learning'),
    ('People & HR', r'human resources|\bhr\b|talent'),
    ('Programs & events', r'program|event|conference|project (coordinator|manager|specialist)|coordinator'),
    ('Operations & administration', r'operations|office manager|administrator|administrative|business (manager|officer)|executive assistant|assistant'),
]
_FUNCS = [(label, re.compile(p, re.I)) for label, p in FUNCTIONS]
CORE_FUNCTIONS = {'Data & analytics', 'Marketing & communications', 'Admissions & enrollment', 'Career services',
                  'International programs', 'Strategy & planning', 'Product & digital learning',
                  'Alumni & advancement', 'Programs & events', 'Student services & advising'}
DROP = re.compile(
    r'professor|faculty|lecturer|instructor|teacher|post-?doc|fellowship|\bfellow\b|scientist|physician|surgeon|'
    r'nurs|clinical|medical|pharm|therap|dental|dentist|radiolog|imaging|sonograph|patient|hospital|'
    r'lab(oratory)? (tech|assistant|manager|coordinator)|technologist|technician|police|security officer|guard|'
    r'custod|janitor|housekeep|maintenance|electrician|plumb|carpent|hvac|mechanic|grounds|landscap|cook|chef|'
    r'culinary|dishwash|food service|driver|\bcoach\b|athletic trainer|chaplain|lifeguard|engineer|developer|'
    r'programmer|software|sysadmin|system administrator|network|devops|it support|help ?desk|desktop|cyber|'
    r'\bRN\b|\bLVN\b|\bCNA\b|\bMA\b /|transplant|surg|perioperative|anesthes|cardio|oncolog|radiat|psychiatr|'
    r'ambulatory|pathology|technolog|information technology|\bIT\b|clinic|emergency|internal medicine|neuroscien|'
    r'case manag|care coordinat|pediatric|infusion|dialysis|\bICU\b|urgent care|bed management|per diem|\bPRN\b|'
    r'\b\d{1,2} hours? (days|nights|evenings)|medical center|health system|'
    r'\bdean\b|vice (president|provost|chancellor)|provost|chancellor|\bchief\b(?! of staff)|\bc[ifot]o\b|\bvc\b|\bcounsel\b|attorney|'
    r'(?<!assistant )director|veterinar|animal|research assistant(?! .*(data|survey))|phlebotom|respiratory',
    re.I)
# A job FOR students (not a staff job that serves students, like "Student Affairs Manager").
STUDENT = re.compile(r'student (worker|assistant|employee|associate|ambassador|aide|intern|tutor|leader|staff|clerk|'
                     r'caller|mentor|researcher|technician|supervisor|wage|position|job)s?\b|'
                     r'graduate (student )?(assistant|researcher|intern)|\bGA\b|work[- ]study|\bFWS\b|'
                     r'teaching assistant|undergraduate (student|assistant|researcher)|peer (advisor|mentor|tutor)|'
                     r'^\W*intern\b|\bintern\b(?!ship)|\(student\)', re.I)
SENIOR = re.compile(r'\bsenior\b|\bsr\.?\b|\blead\b|\bprincipal\b|\bmanager\b|associate director', re.I)
ADMIN = re.compile(r'administrative assistant|office assistant|receptionist|front desk', re.I)

_MONEY = re.compile(r'\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?\s*(k\b)?', re.I)
_NUM = re.compile(r'(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?\s*(k\b)?', re.I)   # numbers inside a matched amount
_RANGE = re.compile(r'\$\s?[\d,]+(?:\.\d{2})?\s*k?\s*(?:-|–|—|to)\s*\$?\s?[\d,]+(?:\.\d{2})?\s*k?', re.I)
_PAY_WORDS = re.compile(r'salary|pay|rate|compensation|hiring range|wage|hourly|annual|per hour|/hr|per year|'
                        r'base|range', re.I)


def _num(m):
    v = float(m.group(1).replace(',', '') + ('.' + m.group(2) if m.group(2) else ''))
    return v * 1000 if m.group(3) else v


def pay_of(desc: str):
    """-> dict(min, max, unit, annual_max, text) or None. Hourly when amounts < 300; monthly when
    'month' is next to the amount; otherwise annual. Ignores dollar figures with no pay words nearby."""
    for m in list(_RANGE.finditer(desc or '')) + list(_MONEY.finditer(desc or '')):
        before, after = desc[max(0, m.start() - 90):m.start()], desc[m.end():m.end() + 30]
        if re.match(r'\s?(m|mm|b|bn)\b|\s?(million|billion)', after, re.I):
            continue                      # "$100M annually" is fundraising, not pay
        window = before + m.group(0) + after
        unit_after = re.search(r'per (hour|year|annum|month)|/\s?h(ou)?r|hourly|annual|a year|an hour|/yr', after, re.I)
        if not (_PAY_WORDS.search(before) or unit_after):
            continue                      # pay words must sit right before the amount (or a unit right after)
        if re.search(r'project|contract|capital|portfolio|budget|purchas|exceed|revenue|donation|gift|campaign|rais|'
                     r'award|grant|endowment|million|billion|transaction|spend|assets', window, re.I) and \
                not re.search(r'salary|pay range|hiring range|compensation|wage|pay rate', before, re.I):
            continue                      # a project/budget amount, not a salary
        nums = [_num(x) for x in _NUM.finditer(m.group(0))]
        nums = [n for n in nums if n >= 7]
        if not nums:
            continue
        lo, hi = min(nums), max(nums)
        after = desc[m.end():m.end() + 40].lower()
        if 120 < hi < 300:
            continue                          # not a plausible hourly rate (often a truncated "$160K")
        if hi < 300:
            unit, annual = 'hour', hi * 2080
        elif 'month' in after or 'monthly' in window.lower() and hi < 20000:
            unit, annual = 'month', hi * 12
        elif hi < 15000:
            continue                          # a stipend or one-off amount, not a salary
        else:
            unit, annual = 'year', hi
        factor = {'hour': 2080, 'month': 12, 'year': 1}[unit]
        if (lo + hi) / 2 * factor > 300000:
            continue                      # implausible for the roles this track keeps
        # The middle of the range ranks jobs: very wide ranges ($39k–$292k) don't inflate it.
        return {'min': lo, 'max': hi, 'unit': unit, 'annual_max': round(annual),
                'annual_mid': round((lo + hi) / 2 * factor), 'text': m.group(0).strip()}
    return None


def function_of(title: str):
    for label, rx in _FUNCS:
        if rx.search(title or ''):
            return label
    return None


def score(r, prefs, floor_year=70000, floor_hour=22):
    """0–100 ordering for the Universities tab: pay first, then fit. Not a probability."""
    from ..hub.db import pref_adjust
    parts = {}
    parts['function'] = 30 if r['uni_function'] in CORE_FUNCTIONS else 20
    if ADMIN.search(r['title']):
        parts['function'] = 12
    pay = r.get('pay') or {}
    mid = pay.get('annual_mid')
    if r['employment'] == 'student':
        h = mid / 2080 if mid else None
        parts['pay'] = 12 if h is None else 30 if h >= 25 else 22 if h >= 20 else 14 if h >= 17 else 8
        parts['now'] = 15
    else:
        parts['pay'] = 12 if mid is None else 35 if mid >= 100000 else 30 if mid >= 85000 else 24 if mid >= 70000 \
            else 14 if mid >= 55000 else 6
        parts['now'] = 8
    parts['edge'] = 5 if re.search(r'korea|international|global|asia', r['title'], re.I) else 0
    parts['seniority'] = -5 if SENIOR.search(r['title']) else 0
    parts['her_ratings'] = pref_adjust(prefs, r['lane'], 'education')
    # Student jobs compare the hourly equivalent with her hourly line; staff jobs the yearly amount.
    good = bool(mid) and (mid / 2080 >= floor_hour if r['employment'] == 'student' else mid >= floor_year)
    return round(max(0, min(100, sum(parts.values()))), 1), parts, good


# ------------------------------------------------------------------ harvest
def _workday_search(u, a, cap=100):
    host = a.get('host') or f"{a['tenant']}.wd{a['wd']}.myworkdayjobs.com"
    api = f"https://{host}/wday/cxs/{a['tenant']}/{a['site']}/jobs"
    public = (f"https://{host}/recruiting/{a['tenant']}/{a['site']}" if 'myworkdaysite' in host
              else f"https://{host}/en-US/{a['site']}")
    seen, rows = set(), []
    for q in QUERIES:
        offset, total = 0, 1
        while offset < min(total, cap):
            d = net.get(api, data={'appliedFacets': {}, 'limit': 20, 'offset': offset, 'searchText': q})
            if offset == 0:
                total = d.get('total') or 0
            batch = d.get('jobPostings') or []
            for j in batch:
                path = j.get('externalPath') or ''
                if not j.get('title') or path in seen:
                    continue
                seen.add(path)
                rows.append(sources._row(source='university', board=f"workday:{a['tenant']}:{a.get('kind', 'staff')}",
                                         company=u['name'],
                                         title=j['title'], location=j.get('locationsText', ''), url=public + path,
                                         posted=sources._wd_posted(j.get('postedOn')), date_precision='relative',
                                         req_id=(j.get('bulletFields') or [path])[0],
                                         detail={'kind': 'workday', 'api': f"https://{host}/wday/cxs/{a['tenant']}/{a['site']}" + path}))
            if not batch:
                break
            offset += len(batch)
    return rows


_MONTHS = {m: i for i, m in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split(), 1)}


def _tag(xml, name):
    m = re.search(r'<%s(?:\s[^>]*)?>(.*?)</%s>' % (name, name), xml, re.S)
    if not m:
        return ''
    v = m.group(1).strip()
    if v.startswith('<![CDATA['):
        v = v[9:-3]
    return v


def _feed_date(v):
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', v or '')
    if m:
        return '-'.join(m.groups())
    m = re.search(r'(\d{1,2}) (\w{3})\w* (\d{4})', v or '')
    if m and m.group(2)[:3].lower() in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(2)[:3].lower()]:02d}-{int(m.group(1)):02d}"
    return None


def _feed(u, a):
    """RSS 2.0 or Atom feeds (PageUp, PeopleAdmin, SuccessFactors, SilkRoad, Taleo, CUNY …).
    `filter`: keep items whose text contains every given value (one campus of a shared board)."""
    hdr = {'Accept': 'application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8'}
    urls = [a['feed'] + q for q in QUERIES] if a.get('per_query') else [a['feed']]   # feeds capped at ~20 items
    items, seen = [], set()
    for url in urls:
        for it in re.split(r'<item[\s>]|<entry[\s>]', net.get(url, raw=True, timeout=60, headers=hdr))[1:]:
            key = _tag(it, 'link') or _tag(it, 'guid') or it[:200]
            if key not in seen:
                seen.add(key)
                items.append(it)
    rows = []
    for it in items:
        if a.get('filter') and not all(str(v).lower() in it.lower() for v in a['filter'].values()):
            continue
        title = text(_tag(it, 'title'))
        link = _tag(it, 'link') or (re.search(r'<link[^>]*href="([^"]+)"', it) or [None, ''])[1]
        extra = ' '.join(text(v) for k, v in re.findall(r'<(job:\w+|pa:\w+)>(.*?)</\1>', it, re.S))
        desc = text(_tag(it, 'description') or _tag(it, 'content') or _tag(it, 'summary')) + ' ' + extra
        loc = text(_tag(it, 'job:location') or '') or f"{u.get('city', '')}, {u.get('state', '')}"
        if not title or not link:
            continue
        rows.append(sources._row(source='university', board=f"feed:{u['short']}:{a.get('kind', 'staff')}",
                                 company=u['name'], title=title, location=loc, url=text(link),
                                 posted=_feed_date(_tag(it, 'pubDate') or _tag(it, 'published') or _tag(it, 'updated')),
                                 description=desc,
                                 detail={'kind': 'html', 'url': text(link)} if a.get('fetch_detail') else None))
    return rows


def _jibe_pay(x):
    """UC campuses put pay in Jibe tag fields ("USD $29.30/Hr.", "USD $44.64/Hr.")."""
    vals = [str(v) for k, vs in x.items() if k.startswith('tags') and isinstance(vs, list) for v in vs if '$' in str(v)]
    nums = [float(n.replace(',', '')) for v in vals for n in re.findall(r'\$\s?([\d,]+(?:\.\d+)?)', v)]
    if not nums:
        return ''
    blob = ' '.join(vals).lower()
    unit = 'per hour' if re.search(r'/hr|hour', blob) else 'per month' if re.search(r'/mo|month', blob) else 'per year'
    return f' Salary range: ${min(nums):,.2f} - ${max(nums):,.2f} {unit}.'


def _jibe_all(u, a):
    rows = []
    for page in range(1, a.get('max_pages', 10) + 1):
        d = net.get(f"https://{a['host']}/api/jobs?page={page}&limit=100")
        for j in d.get('jobs', []):
            x = j.get('data') or {}
            rows.append(sources._row(source='university', board=f"jibe:{a['host']}:staff", company=u['name'],
                                     title=x.get('title'), url=f"https://{a['host']}/jobs/{x.get('slug') or x.get('req_id')}",
                                     location=x.get('full_location') or f"{x.get('city', '')}, {x.get('state', '')}",
                                     posted=sources._day(x.get('posted_date') or x.get('create_date')),
                                     description=text(x.get('description')) + _jibe_pay(x)))
        if not d.get('jobs') or page * 100 >= (d.get('totalCount') or 0):
            break
    return rows


def _oracle_q(u, a):
    seen, rows = set(), []
    for q in QUERIES:
        found, _ = sources.oracle(u['name'], a['host'], site=a['site'], keyword=q)
        for r in found:
            if r['url'] not in seen:
                seen.add(r['url'])
                r['source'] = 'university'
                r['board'] = f"oracle:{a['host']}:staff"
                r['detail'] = {'kind': 'oracle', 'host': a['host'], 'site': a['site'], 'id': r['req_id']}
                rows.append(r)
    return rows


def _sr_all(u, a):
    rows, offset, total = [], 0, 1
    while offset < min(total, 1000):
        d = net.get(f"https://api.smartrecruiters.com/v1/companies/{a['token']}/postings?limit=100&offset={offset}")
        total = d.get('totalFound') or 0
        for j in d.get('content', []):
            loc = j.get('location') or {}
            rows.append(sources._row(source='university', board=f"smartrecruiters:{a['token']}:staff", company=u['name'],
                                     title=j.get('name'), location=', '.join(x for x in (loc.get('city'), loc.get('region')) if x),
                                     url=f"https://jobs.smartrecruiters.com/{a['token']}/{j.get('id')}",
                                     posted=sources._day(j.get('releasedDate')),
                                     detail={'kind': 'smartrecruiters', 'token': a['token'], 'id': j.get('id')}))
        if not d.get('content'):
            break
        offset += 100
    return rows


def _sitemap(u, a):
    """Job URLs from a career site's sitemap (iCIMS, PeopleClick, Paradox). The title comes from
    the URL; the page itself is fetched later (cached) for the text and pay."""
    from urllib.parse import unquote
    xml = net.get(a['sitemap'], raw=True, timeout=60)
    rows = []
    for loc, lastmod in re.findall(r'<loc>([^<]+)</loc>(?:\s*<lastmod>([^<]+)</lastmod>)?', xml):
        m = re.search(a['pattern'], loc)
        if not m:
            continue
        slug = unquote(m.group('slug')).replace('-', ' ').replace('_', ' ')
        title = re.sub(r'\s+', ' ', slug).strip().title()
        page = loc + ('?in_iframe=1' if a.get('iframe') else '')
        rows.append(sources._row(source='university', board=f"sitemap:{u['short']}:staff", company=u['name'],
                                 title=title, location=f"{u.get('city', '')}, {u.get('state', '')}", url=loc,
                                 posted=_feed_date(lastmod), detail={'kind': 'html', 'url': page}))
    return rows


def _oracle_detail(d):
    """Oracle HCM postings are rendered by JavaScript; the REST detail call has the text."""
    u = (f"https://{d['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all"
         f"&onlyData=true&finder=ById;Id=%22{d['id']}%22,siteNumber={d['site']}")
    items = net.get(u).get('items') or [{}]
    x = items[0]
    parts = [x.get(k) or '' for k in ('ExternalDescriptionStr', 'ExternalResponsibilitiesStr',
                                      'ExternalQualificationsStr', 'CorporateDescriptionStr')]
    return text(' '.join(parts))


def _html_detail(url):
    """Posting text from a web page: JSON-LD JobPosting when present, else the page text."""
    page = net.get(url, raw=True, timeout=30)
    for blob in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            d = json.loads(blob)
        except ValueError:
            continue
        for x in (d if isinstance(d, list) else [d]):
            if isinstance(x, dict) and x.get('@type') == 'JobPosting':
                sal = x.get('baseSalary') or {}
                v = (sal.get('value') or {}) if isinstance(sal, dict) else {}
                pay = ''
                if v.get('minValue') or v.get('maxValue') or v.get('value'):
                    unit = {'HOUR': 'per hour', 'YEAR': 'per year', 'MONTH': 'per month'}.get(str(v.get('unitText')).upper(), '')
                    lo, hi = v.get('minValue') or v.get('value'), v.get('maxValue') or v.get('value')
                    pay = f" Salary range: ${float(lo):,.2f} - ${float(hi):,.2f} {unit}."
                return text(x.get('description') or '') + pay
    body = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', page, flags=re.S)
    return text(body)[:10000]


def _generic(u, a):
    """Other systems reuse the internship adapters' list calls with a broad keyword."""
    fn = {'oracle': sources.oracle, 'eightfold': sources.eightfold, 'phenom': sources.phenom,
          'jibe': sources.jibe, 'greenhouse': sources.greenhouse, 'lever': sources.lever,
          'smartrecruiters': sources.smartrecruiters}.get(a['type'])
    if not fn:
        raise ValueError('no adapter for ' + a['type'])
    rows, _ = fn(company=u['name'], **{k: v for k, v in a.items() if k not in ('type', 'company')})
    for r in rows:
        r['source'] = 'university'
    return rows


ADAPTERS = {'workday': _workday_search, 'feed': _feed, 'jibe_all': _jibe_all, 'oracle_q': _oracle_q,
            'sr_all': _sr_all, 'sitemap': _sitemap}
EXTRA = {}          # system name -> function(u, a) registered by sources for university-only systems


def harvest(log=print, workers=8):
    reg = json.loads(paths.registry('universities.json').read_text())
    tasks = [(u, a) for u in reg['universities'] for a in u.get('boards', []) if a.get('verified', True)]
    rows, health = [], {'sources': 0, 'ok': 0, 'failed': 0, 'blocked': 0, 'errors': []}

    def one(t):
        u, a = t
        f = ADAPTERS.get(a['type']) or EXTRA.get(a['type']) or _generic
        return f(u, a)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [(t, ex.submit(one, t)) for t in tasks]
        for (u, a), f in futs:
            health['sources'] += 1
            try:
                rows += f.result()
                health['ok'] += 1
            except net.Blocked:
                health['blocked'] += 1
            except Exception as e:  # noqa: BLE001 — one board never stops the scan
                health['failed'] += 1
                health['errors'].append(f"{u['name']}: {type(e).__name__}: {str(e)[:70]}")
    health['errors'] = health['errors'][:10]
    log(f"[universities] {health['ok']}/{health['sources']} boards answered, {len(rows)} postings")
    return rows, health


def home_university():
    """Her own school (data/profile.json → home_university). Student jobs elsewhere need
    enrollment there, so only the home school's student jobs are kept."""
    try:
        return json.loads(paths.data('profile.json').read_text()).get('home_university') or ''
    except (OSError, ValueError):
        return ''


def classify(rows, home=None):
    home = home_university() if home is None else home
    kept, dropped = [], {}
    seen = set()
    for r in rows:
        title = r['title']
        if r['url'] in seen:
            continue
        seen.add(r['url'])
        why = None
        if DROP.search(title):
            why = 'faculty, clinical, trades, IT engineering or senior leadership'
        elif us_status(r['location']) == 'foreign':
            why = 'outside the US'
        fn = function_of(title) if not why else None
        if not why and not fn and home and r['company'] == home and STUDENT.search(title):
            fn = 'Campus student job'      # at her own school any reasonable student job counts
        if not why and not fn:
            why = 'function outside her interests'
        if why:
            dropped[why] = dropped.get(why, 0) + 1
            continue
        r.update(uni_function=fn, lane=lane_of(title)[0], industry='education', tier='university',
                 employment='student' if (STUDENT.search(title) or r.get('board', '').endswith(':student')) else 'staff',
                 university=r['company'],
                 job_key='uni|' + canon_company(r['company']) + '|' + re.sub(r'\W+', '', r['url'].lower())[-60:])
        if r['employment'] == 'student' and home and r['company'] != home:
            dropped['student job at another school (needs enrollment there)'] = \
                dropped.get('student job at another school (needs enrollment there)', 0) + 1
            continue
        kept.append(r)
    return kept, dropped


def enrich(rows, workers=8, log=print):
    """Full text + pay for each posting, cached by URL (a posting's page is fetched once)."""
    cache_p = paths.state('uni_details.json')
    try:
        cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    except ValueError:
        cache = {}
    todo = [r for r in rows if r['url'] not in cache and r.get('detail')]

    def one(r):
        try:
            if (r.get('detail') or {}).get('kind') == 'oracle':
                full = _oracle_detail(r['detail'])
                return r['url'], {'description': full[:6000], 'pay': pay_of(full), 'fetched': date.today().isoformat()}
            if (r.get('detail') or {}).get('kind') == 'html':
                full = _html_detail(r['detail']['url'])
                return r['url'], {'description': full[:6000], 'pay': pay_of(full), 'fetched': date.today().isoformat()}
            desc, extra = sources.fetch_detail(r)
            return r['url'], {'description': (desc or '')[:6000], 'pay': pay_of(desc or ''), 'fetched': date.today().isoformat()}
        except Exception:  # noqa: BLE001
            return r['url'], {'description': '', 'fetched': date.today().isoformat(), 'error': True}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for url, rec in ex.map(one, todo):
            cache[url] = rec
    for r in rows:
        rec = cache.get(r['url']) or {}
        full = r.get('description') or ''
        # pay is read from the FULL text (UCLA, for one, puts it at the very end); only a short
        # excerpt is stored
        r['pay'] = pay_of(full) or rec.get('pay') or pay_of(rec.get('description') or '')
        r['description'] = (rec.get('description') or full)[:6000]
        if r['employment'] == 'student' and (r['pay'] or {}).get('unit') == 'year' and \
                (r['pay'].get('annual_mid') or 0) >= 45000:
            r['employment'] = 'staff'     # a full salary means a staff role (e.g. Yale SOM "Teaching Assistant")
    live = {r['url'] for r in rows}
    cache = {u: v for u, v in cache.items() if u in live or v.get('fetched', '') >= date.today().isoformat()[:7]}
    cache_p.write_text(json.dumps(cache, ensure_ascii=False))
    log(f"[universities] fetched {len(todo)} new posting pages (others cached)")
    return rows


def scan(prefs, floor_year=70000, floor_hour=22, log=print):
    rows, health = harvest(log)
    kept, dropped = classify(rows)
    kept = enrich(kept, log=log)
    for r in kept:
        r['prescore'], r['prescore_parts'], r['good_pay'] = score(r, prefs, floor_year, floor_hour)
    kept.sort(key=lambda r: (-((r.get('pay') or {}).get('annual_mid') or 0) if r['good_pay'] else 0, -r['prescore']))
    summary = {'postings_read': len(rows), 'kept': len(kept), 'dropped': dropped, 'health': health,
               'student_jobs': sum(r['employment'] == 'student' for r in kept),
               'good_pay': sum(bool(r['good_pay']) for r in kept),
               'by_university': {}}
    for r in kept:
        summary['by_university'][r['university']] = summary['by_university'].get(r['university'], 0) + 1
    return kept, summary
