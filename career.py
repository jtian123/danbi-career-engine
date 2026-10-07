#!/usr/bin/env python3
"""Danbi's career engine: internship discovery, the Career Hub, and source-backed résumés.

Daily:   python3 career.py scan            → output/scans/scan-DATE/ (review queue for Claude)
         python3 career.py mark output/scans/scan-DATE   → today's list in the hub
Hub:     python3 career.py hub serve | install | status | open
"""
import argparse
import fcntl
import json
import sys
from pathlib import Path

from src.paths import ROOT, data


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('scan', help='Find new US internships and write a review queue')
    s.add_argument('--queue', type=int, default=40, help='how many jobs Claude reviews (default 40)')
    s.add_argument('--no-linkedin', action='store_true')
    s.add_argument('--no-boards', action='store_true', help='skip the ~1,300 mid-size/startup boards (faster)')
    s.add_argument('--workers', type=int, default=16)
    m = sub.add_parser('mark', help="Validate Claude's reviews and put the day's list into the hub")
    m.add_argument('scan_dir')
    m.add_argument('--reviewed', help='default: SCAN_DIR/reviewed.json')
    m.add_argument('--force', action='store_true', help='re-apply reviews already marked (after edits or new profile facts)')
    h = sub.add_parser('hub', help='The Career Hub (local dashboard, http://127.0.0.1:7768)')
    h.add_argument('action', choices=['serve', 'install', 'uninstall', 'status', 'open'])
    h.add_argument('--port', type=int, default=7768)
    lead = sub.add_parser('lead', help='Add a job she found herself (Handshake, a fair, a referral)')
    lead.add_argument('company'); lead.add_argument('title')
    lead.add_argument('--url', default=''); lead.add_argument('--source', default='handshake')
    lead.add_argument('--deadline', default=''); lead.add_argument('--notes', default='')
    st = sub.add_parser('status', help='Set a job status from the command line')
    st.add_argument('job_id', type=int)
    st.add_argument('status')
    nv = sub.add_parser('never', help='Never show this company again')
    nv.add_argument('company')
    sub.add_parser('prefs', help="Print what her ratings say so far (for Claude's planning)")
    sub.add_parser('campus-sync', help='Load dated USC events from registry/campus.json into the hub')
    sub.add_parser('import-legacy', help='One-time: move the Sep 2026 reviewed shortlist into the hub')

    r = sub.add_parser('resume', help='Prepare a source-backed resume draft and review packet')
    r.add_argument('jd', help='Saved job-description text file')
    r.add_argument('--lane', default='product')
    r.add_argument('--label', default='resume')
    r.add_argument('--proposal', help='Assistant-written selection plan')
    r.add_argument('--plan-only', action='store_true')
    rv = sub.add_parser('resume-revise', help='Build a new revision; preserves the previous draft')
    rv.add_argument('directory'); rv.add_argument('--proposal', required=True)
    rq = sub.add_parser('resume-qa', help='Recheck exact content and rendered artifacts')
    rq.add_argument('directory')
    rf = sub.add_parser('resume-finalize', help='Release only after factual, content and visual checks pass')
    rf.add_argument('directory'); rf.add_argument('--content-review'); rf.add_argument('--visual-review')
    rc = sub.add_parser('resume-compare', help='Select the cleanest reviewed revision of one JD')
    rc.add_argument('directories', nargs='+')
    sub.add_parser('doctor', help='Check this machine has what the engine needs')
    args = p.parse_args()

    if args.command == 'hub':
        from src.hub import service
        return service.run(args.action, args.port)
    if args.command == 'doctor':
        from src import doctor
        return doctor.run()

    data().mkdir(parents=True, exist_ok=True)
    with (data() / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == 'scan':
            from src.scan.run import scan
            out = scan(args.queue, not args.no_boards, not args.no_linkedin, args.workers,
                       log=lambda msg: print(msg, file=sys.stderr, flush=True))
            print(out)
        elif args.command == 'mark':
            from src.scan.run import mark
            try:
                mark(args.scan_dir, args.reviewed, force=args.force)
            except ValueError as e:
                p.error(str(e))
        elif args.command == 'lead':
            from src.hub import db
            row = db.add_lead(args.company, args.title, args.url, args.source, notes=args.notes, deadline=args.deadline)
            print(f"Added #{row['id']}: {row['company']} — {row['title']} (saved)")
        elif args.command == 'status':
            from src.hub import db
            row = db.set_status(args.job_id, args.status)
            print(f"#{row['id']} {row['company']} — {row['title']}: {row['status']}")
        elif args.command == 'never':
            from src.hub import db
            print('added' if db.never_add(args.company) else 'already listed')
        elif args.command == 'prefs':
            from src.hub import db
            print(json.dumps(db.preferences(), indent=2))
        elif args.command == 'campus-sync':
            from src.hub import db
            print(db.sync_campus(json.loads((ROOT / 'registry/campus.json').read_text())), 'new events')
        elif args.command == 'import-legacy':
            from src.hub import legacy
            print(legacy.import_legacy())
        elif args.command.startswith('resume'):
            from src.resume import pipeline
            try:
                if args.command == 'resume':
                    print(pipeline.build(args.jd, args.lane, args.label, args.proposal, args.plan_only))
                elif args.command == 'resume-revise':
                    d = Path(args.directory)
                    print(pipeline.build(d / 'job_description.txt', label='revision', proposal=args.proposal, previous=d))
                elif args.command == 'resume-qa':
                    *_, findings = pipeline.check_build(args.directory)
                    print(json.dumps(findings, indent=2))
                elif args.command == 'resume-finalize':
                    print(pipeline.finalize(args.directory, args.content_review, args.visual_review))
                elif args.command == 'resume-compare':
                    print(pipeline.compare_builds(args.directories))
            except (ValueError, RuntimeError, KeyError, FileNotFoundError) as error:
                p.error(str(error))
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
