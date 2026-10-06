"""One-time move of the September 2026 reviewed shortlist (data/jobs.json, built before
the hub existed) into the hub, on the day it was reviewed. Idempotent."""
from __future__ import annotations

import json
from datetime import date

from .. import paths
from ..engine import load, load_lanes, rank
from ..scan.classify import employer, industry_of
from ..scan.state import job_key
from . import db


def import_legacy() -> str:
    jobs = load(paths.data('jobs.json'), [])
    profile = load(paths.data('profile.json'))
    if not jobs or not profile:
        return 'nothing to import'
    day = '2026-09-16'
    ranked = rank(jobs, profile, load_lanes(), load(paths.data('feedback.json'), {}), date.fromisoformat(day))
    rows = []
    for j in ranked:
        review = {k: j.get(k) for k in ('why', 'gaps', 'task', 'conversion', 'next_step', 'evidence_note',
                                        'verification', 'verified_at', 'technical_level', 'score_components',
                                        'checks', 'blockers', 'degree_rule')}
        review['decision'] = 'recommend' if j['bucket'] in ('apply', 'stretch') else 'check'
        rows.append(dict(job_key=job_key(j['company'], j['title']), company=j['company'], title=j['title'],
                         url=j['url'], location=j.get('location'), lane=j['lane'], industry=industry_of(j['company']),
                         tier=employer(j['company']).get('tier') or '', source='review (Sep 2026)', board='legacy',
                         season=j.get('timing'), posted=j.get('posted_at'), pay=j.get('pay'),
                         deadline=j.get('deadline'), reviewed=True, fit=j['score'], review=review,
                         bucket=j['bucket']))
    n = db.upsert_surfaced(rows, day, 'legacy-2026-09-16', origin='legacy')
    db.save_day(day, {'scan_id': 'legacy-2026-09-16', 'reviewed': len(rows),
                      'note': 'First researched shortlist (built before the hub). Many deadlines have passed.'})
    return f'{len(rows)} reviewed jobs from Sep 16 → hub ({n})'
