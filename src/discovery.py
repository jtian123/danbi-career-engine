"""Public ATS discovery, adapted from James's source portfolio and title-first design.

No imported James state, hardcoded personal filters, paid APIs or credentials.
New candidates are deliberately unreviewed. Search plan complements the finite boards.
"""
from __future__ import annotations
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import quote

from .engine import dedupe

INTERN = re.compile(r'\b(intern|internship|co[ -]?op|student|apprentice)\b', re.I)
OUT_OF_SCOPE = re.compile(r'\b(machine learning engineer|software engineer|research scientist|phd|ph\.d|postdoc)\b', re.I)


def fetch(url, body=None):
    headers = {'User-Agent': 'DanbiCareerEngine/1.0 (public job discovery)', 'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    request = Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def text(value):
    # Greenhouse sometimes entity-encodes the entire HTML fragment.
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html.unescape(html.unescape(value or '')))).strip()


def classify(title, lanes):
    s = title.lower()
    for lane in lanes:
        if any(re.search(r'\b' + re.escape(term.lower()) + r'\b', s) for term in lane['match_terms']):
            return lane['id']
    return None


def query_plan(lanes, today=None):
    today = today or date.today()
    next_year = today.year + 1
    return [{'lane': lane['id'], 'keyword': keyword,
             'queries': [f'"{keyword}" internship "{next_year}" "United States"',
                         f'"{keyword}" intern ("{today.year}" OR "spring {next_year}" OR "co-op") (US OR remote)',
                         f'"{keyword}" (intern OR internship) (masters OR graduate) site:myworkdayjobs.com'],
             'handshake': f'{keyword}; United States; Internship; include part time; check graduate eligibility',
             'search_url': 'https://www.google.com/search?q=' + quote(f'"{keyword}" internship {next_year} United States')}
            for lane in lanes for keyword in lane['keywords']]


def harvest(board, lanes):
    kind, token = board['type'], board['token']
    items, diag = [], {'company': board['company'], 'type': kind, 'token': token}
    try:
        if kind == 'greenhouse':
            url = f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true'
            raw = fetch(url)
            for j in raw['jobs']:
                items.append({'title': j['title'], 'location': j['location']['name'],
                    'url': j['absolute_url'], 'requisition_id': str(j['id']),
                    'description': text(j.get('content')), 'posted_at': j.get('first_published'),
                    'updated_at': j.get('updated_at'), 'country': None})
        elif kind == 'lever':
            url = f'https://api.lever.co/v0/postings/{token}?mode=json'
            raw = fetch(url)
            for j in raw:
                items.append({'title': j['text'], 'location': j.get('categories', {}).get('location', ''),
                    'url': j['hostedUrl'], 'requisition_id': j['id'],
                    'description': text(j.get('descriptionPlain') or j.get('description')),
                    'posted_at': j.get('createdAt'), 'country': j.get('country')})
        elif kind == 'ashby':
            url = f'https://api.ashbyhq.com/posting-api/job-board/{token}'
            raw = fetch(url)
            for j in raw['jobs']:
                if j.get('isListed') is False:
                    continue
                items.append({'title': j['title'], 'location': j.get('location', ''),
                    'url': j.get('jobUrl') or j['applyUrl'], 'requisition_id': j['id'],
                    'description': text(j.get('descriptionPlain') or j.get('descriptionHtml')),
                    'posted_at': j.get('publishedAt'), 'country': (j.get('address') or {}).get('postalAddress', {}).get('addressCountry')})
        elif kind == 'workday':
            base = f'https://{token}.wd{board["wd"]}.myworkdayjobs.com'
            url = base + f'/wday/cxs/{token}/{board["site"]}/jobs'
            offset, total = 0, 1
            while offset < total and offset < 200:
                raw = fetch(url, {'appliedFacets': {}, 'limit': 20, 'offset': offset, 'searchText': 'intern'})
                total = raw['total']
                batch = raw['jobPostings']
                if not batch:
                    break
                for j in batch:
                    items.append({'title': j['title'], 'location': j.get('locationsText', ''),
                        'url': base + '/en-US/' + board['site'] + j['externalPath'],
                        'requisition_id': (j.get('bulletFields') or [j['externalPath']])[0],
                        'description': '', 'posted_at': j.get('postedOn'), 'date_precision': 'relative', 'country': None})
                offset += len(batch)
            diag['truncated'] = total > offset
        else:
            raise ValueError('Unknown ATS type: ' + kind)
        diag.update(status='ok', inventory=len(items), endpoint=url)
    except Exception as exc:
        # One request only on an access block; no retries or alternate identities.
        diag.update(status='blocked' if getattr(exc, 'code', 0) in (403, 429, 999) else 'error',
                    error=type(exc).__name__ + ': ' + str(exc), partial_inventory=len(items))
    candidates = []
    for j in items:
        lane = classify(j['title'], lanes)
        if lane and INTERN.search(j['title']) and not OUT_OF_SCOPE.search(j['title']):
            j.update(company=board['company'], lane=lane, reviewed=False,
                     verification='ats_inventory', discovered_at=datetime.now(timezone.utc).isoformat(),
                     source=kind, country=j.get('country') or 'unknown',
                     review_required=['Confirm US location', 'Read full JD and degree eligibility',
                                      'Verify active apply path, pay units, workload and learning value'])
            candidates.append(j)
    diag['candidates'] = len(candidates)
    return candidates, diag


def discover(boards, lanes):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda b: harvest(b, lanes), boards))
    return {'schema_version': 1, 'run_at': datetime.now(timezone.utc).isoformat(),
            'coverage_note': 'Finite public-board sample. Web research and signed-in USC Handshake are separate required lanes. No completeness claim.',
            'sources': [diag for _, diag in results],
            'candidates': dedupe([j for items, _ in results for j in items]),
            'web_search_plan': query_plan(lanes)}
