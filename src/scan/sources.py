"""Public job sources. Each adapter returns (rows, diagnostics).

A row is one posting, normalized:
  source, board, company, title, location, url, posted (YYYY-MM-DD|None),
  date_precision, req_id, description, pay_text, degrees, terms, detail
`detail` tells enrich() how to fetch the full posting later.

Rules carried over from James's scanner (see docs/DISCOVERY.md):
  * dates come from first-publish fields, never updated_at;
  * a 403/429/999 is a block: record it, stop that source, never retry or evade;
  * LinkedIn guest search is capped at 20 requests a run, 2 s apart;
  * SmartRecruiters answers 200 + empty for ANY slug, so empty never proves a board.
"""
from __future__ import annotations

import html
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import net
from .classify import text

INTERN_WORD = re.compile(r'\b(intern|interns|internship|co-?op|student|apprentice)', re.I)


def _day(value):
    if value in (None, ''):
        return None
    try:
        if isinstance(value, (int, float)):
            v = float(value)
            if v > 1e11:
                v /= 1000.0
            return datetime.fromtimestamp(v, tz=timezone.utc).date().isoformat()
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')[:25]).date().isoformat()
    except (ValueError, OSError, OverflowError):
        m = re.match(r'(\d{4}-\d{2}-\d{2})', str(value))
        return m.group(1) if m else None


def _row(**kw):
    base = dict(source='', board='', company='', title='', location='', url='', posted=None,
                date_precision='absolute', req_id='', description='', pay_text='', degrees=None,
                terms=None, detail=None, deadline=None, intern_hint=False)
    base.update(kw)
    base['title'] = html.unescape(str(base['title'] or '')).strip()
    base['company'] = html.unescape(urllib.parse.unquote(str(base['company'] or ''))).strip()
    return base


# ------------------------------------------------------------------ standard ATS boards
def greenhouse(company, token, **_):
    d = net.get(f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs')
    rows = [_row(source='ats', board=f'greenhouse:{token}', company=company or j.get('company_name') or token,
                 title=j.get('title'), location=(j.get('location') or {}).get('name', ''), url=j.get('absolute_url'),
                 posted=_day(j.get('first_published')), req_id=str(j.get('id')),
                 deadline=_day(j.get('application_deadline')),
                 detail={'kind': 'greenhouse', 'token': token, 'id': j.get('id')})
            for j in d.get('jobs', [])]
    return rows, len(rows)


def lever(company, token, **_):
    d = net.get(f'https://api.lever.co/v0/postings/{token}?mode=json')
    rows = []
    for j in d:
        parts = [j.get('descriptionPlain') or text(j.get('description'))]
        for block in j.get('lists') or []:
            parts.append(block.get('text', '') + '\n' + text(block.get('content')))
        parts.append(j.get('additionalPlain') or '')
        cats = j.get('categories') or {}
        rows.append(_row(source='ats', board=f'lever:{token}', company=company, title=j.get('text'),
                         location=cats.get('location') or ', '.join(cats.get('allLocations') or []),
                         url=j.get('hostedUrl'), posted=_day(j.get('createdAt')), req_id=j.get('id', ''),
                         description='\n'.join(p for p in parts if p)[:12000]))
    return rows, len(rows)


def ashby(company, token, **_):
    d = net.get(f'https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true')
    rows = []
    for j in d.get('jobs', []):
        if j.get('isListed') is False:
            continue
        comp = (j.get('compensation') or {}).get('compensationTierSummary') or ''
        rows.append(_row(source='ats', board=f'ashby:{token}', company=company, title=j.get('title'),
                         location=j.get('location', ''), url=j.get('jobUrl') or j.get('applyUrl'),
                         posted=_day(j.get('publishedAt')), req_id=j.get('id', ''),
                         description=(j.get('descriptionPlain') or text(j.get('descriptionHtml')))[:12000],
                         pay_text=comp, intern_hint=(j.get('employmentType') == 'Intern')))
    return rows, len(rows)


def smartrecruiters(company, token, **_):
    rows, offset, total = [], 0, 1
    while offset < min(total, 300):
        d = net.get(f'https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset={offset}&q=intern')
        total = d.get('totalFound') or 0
        for j in d.get('content', []):
            loc = j.get('location') or {}
            rows.append(_row(source='ats', board=f'smartrecruiters:{token}', company=company, title=j.get('name'),
                             location=', '.join(x for x in (loc.get('city'), loc.get('region'), loc.get('country')) if x),
                             url=f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
                             posted=_day(j.get('releasedDate')), req_id=str(j.get('id')),
                             detail={'kind': 'smartrecruiters', 'token': token, 'id': j.get('id')}))
        if not d.get('content'):
            break
        offset += 100
    return rows, total


_WD_FACET = re.compile(r'\bintern|co-?op|student', re.I)
_WD_FACET_FAMILY = re.compile(r'\bintern|co-?op|student|university|campus|early career', re.I)
_WD_SKIP = re.compile(r'pharmac|medical|nurs|clinical|residen', re.I)


def _wd_facets(facets, out, param=None):
    for f in facets or []:
        p = f.get('facetParameter', param)
        for v in f.get('values', []) or []:
            if 'values' in v:
                _wd_facets([v], out, p)
                continue
            desc = v.get('descriptor', '')
            if p in ('workerSubType', 'timeType', 'workerType', 'Worker_Sub_Type', 'Time_Type') and \
                    _WD_FACET.search(desc) and not _WD_SKIP.search(desc):
                out.setdefault(p, []).append(v['id'])
            elif p in ('jobFamilyGroup', 'jobFamily') and _WD_FACET_FAMILY.search(desc) and not _WD_SKIP.search(desc):
                out.setdefault('_family', {}).setdefault(p, []).append(v['id'])


def workday(company, tenant, site, wd=None, host=None, **_):
    """Prefer the tenant's own 'Intern' worker-type facet (precise); fall back to text
    search. Workday dates are relative ("Posted 3 Days Ago")."""
    host = host or f'{tenant}.wd{wd}.myworkdayjobs.com'
    api = f'https://{host}/wday/cxs/{tenant}/{site}/jobs'
    public = (f'https://{host}/recruiting/{tenant}/{site}' if 'myworkdaysite' in host
              else f'https://{host}/en-US/{site}')
    first = net.get(api, data={'appliedFacets': {}, 'limit': 1, 'offset': 0, 'searchText': ''})
    found = {}
    _wd_facets(first.get('facets'), found)
    family = found.pop('_family', {})
    plans = []
    if found:
        plans.append((dict(found), ''))
    elif family:
        plans.append((dict(family), ''))
    else:
        plans += [({}, '"intern"'), ({}, 'internship')]
    seen, rows, total = set(), [], 0
    for facets, q in plans:
        offset, t = 0, 1
        cap = 300 if facets else 100
        while offset < min(t, cap):
            d = net.get(api, data={'appliedFacets': facets, 'limit': 20, 'offset': offset, 'searchText': q})
            if offset == 0:
                t = d.get('total') or 0
                total += t
            batch = d.get('jobPostings') or []
            for j in batch:
                path = j.get('externalPath') or ''
                if not j.get('title') or path in seen:
                    continue
                seen.add(path)
                rows.append(_row(source='ats', board=f'workday:{tenant}', company=company, title=j.get('title'),
                                 location=j.get('locationsText', ''), url=public + path,
                                 posted=_wd_posted(j.get('postedOn')), date_precision='relative',
                                 req_id=(j.get('bulletFields') or [path])[0],
                                 detail={'kind': 'workday', 'api': f'https://{host}/wday/cxs/{tenant}/{site}' + path}))
            if not batch:
                break
            offset += len(batch)
    return rows, total


def _wd_posted(s):
    s = (s or '').lower()
    now = datetime.now(timezone.utc).date()
    if 'today' in s:
        return now.isoformat()
    if 'yesterday' in s:
        return (now - timedelta(days=1)).isoformat()
    m = re.search(r'(\d+)\+?\s*day', s)
    if m:
        return (now - timedelta(days=int(m.group(1)))).isoformat()
    return None


def eightfold(company, host, domain, api=None, **_):
    """Eightfold career sites. Newer ones (api='pcsx') use /api/pcsx/search."""
    rows = []
    for q in ('intern', 'internship'):
        if api == 'pcsx':
            d = net.get(f'https://{host}/api/pcsx/search?domain={domain}&start=0&num=100&query={q}&sort_by=timestamp')
            positions = (d.get('data') or {}).get('positions') or []
        else:
            d = net.get(f'https://{host}/api/apply/v2/jobs?domain={domain}&start=0&num=100&query={q}')
            positions = d.get('positions', [])
        for p in positions:
            if api == 'pcsx':
                rows.append(_row(source='bigtech', board=f'eightfold:{domain}', company=company,
                                 title=p.get('name') or p.get('title'),
                                 location=p.get('location') or '; '.join(p.get('locations') or []),
                                 url=f"https://{host}" + (p.get('positionUrl') or f"/careers/job/{p.get('id')}"),
                                 posted=_day(p.get('postedTs')), req_id=str(p.get('displayJobId') or p.get('id'))))
                continue
            rows.append(_row(source='bigtech', board=f'eightfold:{domain}', company=company, title=p.get('name'),
                             location=p.get('location') or ', '.join(p.get('locations') or []),
                             url=p.get('canonicalPositionUrl') or f"https://{host}/careers/job/{p.get('id')}",
                             posted=_day(p.get('t_create')), req_id=str(p.get('display_job_id') or p.get('id')),
                             description=text(p.get('job_description') or '')[:12000]))
    return rows, len(rows)


def oracle(company, host, site=None, siteNumber=None, public_site=None, **_):
    site = site or siteNumber
    rows = []
    for q in ('intern', 'internship'):
        u = (f'https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true'
             '&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber=' + site +
             ',limit=100,keyword=' + urllib.parse.quote(q) + ',sortBy=POSTING_DATES_DESC')
        d = net.get(u)
        for it in d.get('items', []):
            for j in it.get('requisitionList', []):
                rows.append(_row(source='bigtech', board=f'oracle:{host}', company=company, title=j.get('Title'),
                                 location=j.get('PrimaryLocation', ''),
                                 url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{public_site or site}/job/{j.get('Id')}",
                                 posted=_day(j.get('PostedDate')), req_id=str(j.get('Id')),
                                 description=text(j.get('ShortDescriptionStr') or '')))
    return rows, len(rows)


def jibe(company, host, **_):
    """iCIMS career sites behind a Jibe front: GET /api/jobs?keywords=… (public JSON)."""
    rows, page = [], 1
    while page <= 5:
        d = net.get(f'https://{host}/api/jobs?keywords=intern&page={page}')
        for j in d.get('jobs', []):
            x = j.get('data') or {}
            rows.append(_row(source='ats', board=f'jibe:{host}', company=company, title=x.get('title'),
                             location=x.get('full_location') or ', '.join(v for v in (x.get('city'), x.get('state'), x.get('country')) if v),
                             url=f"https://{host}/jobs/{x.get('slug') or x.get('req_id')}",
                             posted=_day(x.get('posted_date') or x.get('create_date')), req_id=str(x.get('req_id', '')),
                             description=text(x.get('description'))[:12000]))
        if len(rows) >= (d.get('totalCount') or 0) or not d.get('jobs'):
            break
        page += 1
    return rows, len(rows)


def phenom(company, base, refNum, **_):
    """Phenom career sites: POST {base}/widgets with ddoKey=refineSearch (public JSON)."""
    body = {'lang': 'en_us', 'deviceType': 'desktop', 'country': 'us', 'pageName': 'search-results',
            'ddoKey': 'refineSearch', 'sortBy': '', 'subsearch': '', 'from': 0, 'jobs': True, 'counts': True,
            'all_fields': ['category', 'country', 'city', 'type'], 'size': 100, 'clearAll': False,
            'jdsource': 'facets', 'isSliderEnable': False, 'pageId': 'page20', 'siteType': 'external',
            'keywords': 'intern', 'global': True, 'selected_fields': {}, 'locationData': {}, 'refNum': refNum}
    origin = '/'.join(base.split('/')[:3])
    d = net.get(base.rstrip('/') + '/widgets', data=body, headers={'Origin': origin, 'Referer': origin + '/'})
    rows = []
    for j in (((d.get('refineSearch') or {}).get('data') or {}).get('jobs') or []):
        rows.append(_row(source='ats', board=f'phenom:{refNum}', company=company, title=j.get('title'),
                         location=j.get('cityStateCountry') or j.get('location') or j.get('cityState') or '',
                         url=j.get('applyUrl') or f"{origin}/us/en/job/{j.get('jobSeqNo') or j.get('jobId')}",
                         posted=_day(j.get('postedDate') or j.get('dateCreated')), req_id=str(j.get('jobId') or j.get('reqId') or ''),
                         description=text(j.get('descriptionTeaser') or '')))
    return rows, (d.get('refineSearch') or {}).get('totalHits', len(rows))


def jobscore(company, token, **_):
    d = net.get(f'https://careers.jobscore.com/jobs/{token}/feed.json')
    rows = []
    for j in d.get('jobs', d if isinstance(d, list) else []):
        rows.append(_row(source='ats', board=f'jobscore:{token}', company=company, title=j.get('title'),
                         location=j.get('location') or '', url=j.get('detail_url') or j.get('url') or '',
                         posted=_day(j.get('opened_date') or j.get('created_at')), req_id=str(j.get('id', '')),
                         description=text(j.get('description'))[:12000]))
    return rows, len(rows)


def tiktok(company='TikTok', site='tiktok', **_):
    """lifeattiktok.com's public search (header website-path: tiktok; 'en' = ByteDance). Global
    results: keep the US ones. No posting date in the API (date_precision 'ingestion')."""
    rows, offset, total = [], 0, 1
    public = 'https://lifeattiktok.com/search/' if site == 'tiktok' else 'https://joinbytedance.com/search/'
    while offset < min(total, 3000):
        d = net.get('https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts',
                    data={'keyword': 'intern', 'limit': 100, 'offset': offset, 'recruitment_id_list': [],
                          'job_category_id_list': [], 'subject_id_list': [], 'location_code_list': []},
                    headers={'website-path': site})
        data = d.get('data') or {}
        total = data.get('count') or 0
        batch = data.get('job_post_list') or []
        for j in batch:
            c, places = j.get('city_info') or {}, []
            while c:
                places.append(c.get('en_name') or '')
                c = c.get('parent')
            rows.append(_row(source='bigtech', board=f'tiktok:{site}', company=company, title=j.get('title'),
                             location=', '.join(p for p in places if p).replace('United States of America', 'USA'),
                             url=public + str(j.get('id')), posted=None, date_precision='ingestion',
                             req_id=str(j.get('code') or j.get('id')),
                             intern_hint=((j.get('recruit_type') or {}).get('en_name') == 'Intern'),
                             description=(text(j.get('description')) + '\n\n' + text(j.get('requirement')))[:12000]))
        if not batch:
            break
        offset += len(batch)
        time.sleep(0.3)
    return rows, total


# ------------------------------------------------------------------ big tech custom APIs
def amazon(**_):
    rows = []
    for q in ('intern', 'internship', 'co-op'):
        d = net.get('https://www.amazon.jobs/en/search.json?base_query=' + urllib.parse.quote(q) +
                    '&offset=0&result_limit=100&sort=recent&country%5B%5D=USA')
        for j in d.get('jobs', []):
            try:
                posted = datetime.strptime(j['posted_date'], '%B %d, %Y').date().isoformat()
            except (KeyError, ValueError):
                posted = None
            desc = '\n\n'.join(f'{h}\n{text(j[k])}' for h, k in (('Basic qualifications', 'basic_qualifications'),
                                                                  ('Description', 'description'),
                                                                  ('Preferred qualifications', 'preferred_qualifications'))
                               if j.get(k))
            rows.append(_row(source='bigtech', board='amazon', company='Amazon', title=j.get('title'),
                             location=j.get('normalized_location') or j.get('location', ''),
                             url='https://www.amazon.jobs' + j.get('job_path', ''), posted=posted,
                             req_id=str(j.get('id_icims', '')), description=desc[:12000]))
    return rows, len(rows)


def microsoft(**_):
    rows = []
    for q in ('intern', 'internship'):
        d = net.get('https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com&start=0&num=100&query='
                    + urllib.parse.quote(q) + '&sort_by=timestamp')
        for p in ((d.get('data') or {}).get('positions') or []):
            rows.append(_row(source='bigtech', board='microsoft', company='Microsoft', title=p.get('name') or p.get('title'),
                             location=p.get('location') or ', '.join(p.get('locations') or []),
                             url='https://apply.careers.microsoft.com' + (p.get('positionUrl') or ''),
                             posted=_day(p.get('postedTs')), req_id=str(p.get('displayJobId', ''))))
    return rows, len(rows)


def apple(**_):
    import urllib.request
    r = urllib.request.Request('https://jobs.apple.com/api/v1/csrfToken',
                               headers={'User-Agent': net.UA, 'Referer': 'https://jobs.apple.com/en-us/search'})
    with urllib.request.urlopen(r, timeout=25) as resp:
        tok = resp.headers.get('X-Apple-CSRF-Token')
        cookie = '; '.join(c.split(';')[0] for c in resp.headers.get_all('Set-Cookie') or [])
    if not tok:
        raise RuntimeError('no CSRF token')
    rows = []
    for q in ('intern', 'internship'):
        d = net.get('https://jobs.apple.com/api/v1/search',
                    data={'query': q, 'filters': {'locations': ['postLocation-USA']}, 'page': 1, 'locale': 'en-us',
                          'sort': 'newest', 'format': {'longDate': 'MMMM D, YYYY', 'mediumDate': 'MMM D, YYYY'}},
                    headers={'X-Apple-CSRF-Token': tok, 'Cookie': cookie, 'Origin': 'https://jobs.apple.com',
                             'Referer': 'https://jobs.apple.com/en-us/search'})
        for j in ((d.get('res') or {}).get('searchResults') or []):
            loc = j.get('postingLocationDisplay') or ', '.join(x.get('name', '') for x in (j.get('locations') or []))
            rows.append(_row(source='bigtech', board='apple', company='Apple', title=j.get('postingTitle'),
                             location=loc,
                             url=f"https://jobs.apple.com/en-us/details/{j.get('positionId')}/{j.get('transformedPostingTitle', '')}",
                             posted=_day((j.get('postDateInGMT') or '')[:19]), req_id=str(j.get('positionId', '')),
                             description=text(j.get('jobSummary') or '')))
    return rows, len(rows)


def google(**_):
    import json
    rows = []
    for q in ('intern', 'internship'):
        page = net.get('https://www.google.com/about/careers/applications/jobs/results/?q=' + urllib.parse.quote(q) +
                       '&sort_by=date&location=United%20States', raw=True)
        m = re.search(r"AF_initDataCallback\(\{key:\s*'ds:1'.*?data:(\[.*?\])\s*,\s*sideChannel", page, re.S)
        if not m:
            continue
        blob = json.loads(m.group(1))
        for j in (blob[0] or []):
            try:
                locs = j[9] if isinstance(j[9], list) else []
                loc = '; '.join(x[0] for x in locs if isinstance(x, list) and x and isinstance(x[0], str))
                rows.append(_row(source='bigtech', board='google', company='Google',
                                 title=j[1] if isinstance(j[1], str) else '', location=loc or 'United States',
                                 url=f'https://www.google.com/about/careers/applications/jobs/results/{j[0]}',
                                 posted=_day(j[13][0]) if isinstance(j[13], list) else None, req_id=str(j[0])))
            except (IndexError, TypeError):
                continue
    return rows, len(rows)


BIGTECH = {'amazon': amazon, 'microsoft': microsoft, 'apple': apple, 'google': google, 'tiktok': tiktok}


# ------------------------------------------------------------------ community list
SIMPLIFY = 'https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json'


def simplify(**_):
    """SimplifyJobs' public, community-maintained internship list (GitHub). Active
    rows only. Tech-leaning (data, product, software); employer links are direct."""
    d = net.get(SIMPLIFY, timeout=60)
    rows = []
    for j in d:
        if not j.get('active') or j.get('is_visible') is False:
            continue
        url = re.sub(r'([?&])(utm_[^=&]+|ref|source)=[^&]*', r'\1', j.get('url') or '').rstrip('?&')
        rows.append(_row(source='simplify', board='simplify', intern_hint=True, company=j.get('company_name'), title=j.get('title'),
                         location='; '.join(j.get('locations') or []), url=url,
                         posted=_day(j.get('date_posted')), date_precision='ingestion',
                         req_id=j.get('id', ''), degrees=j.get('degrees') or None, terms=j.get('terms') or None,
                         detail=_detail_from_url(url)))
    return rows, len(d)


# ------------------------------------------------------------------ LinkedIn guest search
LINKEDIN = ('https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={q}'
            '&location=United%20States&f_E=1&f_TPR=r{secs}&start={start}')


def linkedin(queries, days=7, max_requests=20, sleep=2.0, log=print):
    """Internship-filtered (f_E=1) public search. ≤20 requests a run, 2 s apart.
    Any block ends LinkedIn for the run; the caller records it and skips the day."""
    rows, used, by_query = [], 0, {}
    for q in queries:
        for start in (0, 10):
            if used >= max_requests:
                return rows, {'requests': used, 'by_query': by_query}
            time.sleep(sleep)
            used += 1
            page = net.get(LINKEDIN.format(q=urllib.parse.quote(q), secs=days * 86400, start=start), raw=True)
            if 'authwall' in page[:5000].lower():
                raise net.Blocked('authwall')
            cards = page.split('data-entity-urn="urn:li:jobPosting:')[1:]
            for c in cards:
                jid = c.split('"')[0]
                t = re.search(r'base-search-card__title">\s*(.*?)\s*<', c, re.S)
                co = re.search(r'hidden-nested-link[^>]*>\s*(.*?)\s*<', c, re.S)
                loc = re.search(r'job-search-card__location">\s*(.*?)\s*<', c, re.S)
                dt = re.search(r'datetime="(\d{4}-\d{2}-\d{2})"', c)
                if not (t and co):
                    continue
                rows.append(_row(source='linkedin', board='linkedin', intern_hint=True, company=html.unescape(co.group(1)),
                                 title=html.unescape(t.group(1)), location=html.unescape(loc.group(1)) if loc else '',
                                 url=f'https://www.linkedin.com/jobs/view/{jid}', posted=dt.group(1) if dt else None,
                                 req_id=jid))
                rows[-1]['query'] = q
                by_query.setdefault(q, set()).add(rows[-1]['company'])
            if len(cards) < 10:
                break
    return rows, {'requests': used, 'by_query': by_query}


# ------------------------------------------------------------------ full posting text
def _detail_from_url(url):
    u = url or ''
    m = re.search(r'greenhouse\.io/([\w-]+)/jobs/(\d+)', u)
    if m:
        return {'kind': 'greenhouse', 'token': m.group(1), 'id': m.group(2)}
    m = re.search(r'jobs\.lever\.co/([\w-]+)/([0-9a-f-]{36})', u)
    if m:
        return {'kind': 'lever', 'token': m.group(1), 'id': m.group(2)}
    m = re.search(r'jobs\.ashbyhq\.com/([\w.-]+)/([0-9a-f-]{36})', u)
    if m:
        return {'kind': 'ashby', 'token': m.group(1), 'id': m.group(2)}
    m = re.search(r'https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)(/job/.+)', u)
    if m:
        return {'kind': 'workday', 'api': f'https://{m.group(1)}.{m.group(2)}.myworkdayjobs.com/wday/cxs/'
                                          f'{m.group(1)}/{m.group(3)}{m.group(4).split("?")[0]}'}
    return None


def fetch_detail(row):
    """Full posting text for one queued row. Returns (description, extra) or ('', {})."""
    d = row.get('detail') or {}
    k = d.get('kind')
    if k == 'greenhouse':
        j = net.get(f"https://boards-api.greenhouse.io/v1/boards/{d['token']}/jobs/{d['id']}?pay_transparency=true")
        pay = []
        for x in j.get('pay_input_ranges') or []:
            lo, hi = x.get('min_cents'), x.get('max_cents')
            if lo and hi:
                pay.append(f"${lo / 100:,.2f}-${hi / 100:,.2f} {x.get('currency_type') or ''}".replace('.00', '').strip())
        return text(j.get('content')), {'pay_text': '; '.join(pay)}
    if k == 'workday':
        j = net.get(d['api'])
        info = j.get('jobPostingInfo') or {}
        return text(info.get('jobDescription')), {'start_date': info.get('startDate'),
                                                   'can_apply': info.get('canApply')}
    if k == 'smartrecruiters':
        j = net.get(f"https://api.smartrecruiters.com/v1/companies/{d['token']}/postings/{d['id']}")
        secs = ((j.get('jobAd') or {}).get('sections') or {})
        return '\n\n'.join(text((secs.get(s) or {}).get('text')) for s in
                           ('companyDescription', 'jobDescription', 'qualifications', 'additionalInformation')), {}
    if k == 'lever':
        j = net.get(f"https://api.lever.co/v0/postings/{d['token']}/{d['id']}")
        parts = [j.get('descriptionPlain') or text(j.get('description'))]
        for block in j.get('lists') or []:
            parts.append(block.get('text', '') + '\n' + text(block.get('content')))
        return '\n'.join(p for p in parts if p), {}
    if k == 'ashby':
        b = net.get(f"https://api.ashbyhq.com/posting-api/job-board/{d['token']}?includeCompensation=true")
        for j in b.get('jobs', []):
            if j.get('id') == d['id']:
                return j.get('descriptionPlain') or text(j.get('descriptionHtml')), {}
        return '', {'closed_hint': True}
    return '', {}
