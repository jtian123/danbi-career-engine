#!/usr/bin/env python3
"""Danbi's internship discovery, feedback and source-backed resume pipeline."""
import argparse
import json
from pathlib import Path
import fcntl

from src.engine import ROOT, atomic_json, load, merge_feedback, import_reviewed, render


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    r = sub.add_parser('build', help='Render a standalone dashboard from reviewed jobs')
    r.add_argument('--output')
    f = sub.add_parser('feedback', help='Import her exported ratings; rerank and rebuild')
    f.add_argument('file')
    review = sub.add_parser('review-import', help='Validate and upsert explicitly reviewed jobs, then rebuild')
    review.add_argument('file')
    sub.add_parser('queries', help='Generate current title-first web and Handshake research queries')
    d = sub.add_parser('discover', help='Fetch public ATS boards into a review queue (does not auto-approve jobs)')
    d.add_argument('--boards', default=str(ROOT / 'data/boards.json'))
    resume = sub.add_parser('resume', help='Prepare a source-backed resume draft and review packet')
    resume.add_argument('jd', help='Saved job-description text file')
    resume.add_argument('--lane', default='product', choices=['product','commerce','insights','business','demand','finance','data'])
    resume.add_argument('--label', default='resume')
    resume.add_argument('--proposal', help='Assistant-written selection plan; new wording belongs in the master profile first')
    resume.add_argument('--plan-only', action='store_true', help='Prepare content without rendering documents')
    revise = sub.add_parser('resume-revise', help='Build a new revision; preserves the previous draft')
    revise.add_argument('directory')
    revise.add_argument('--proposal', required=True)
    rq = sub.add_parser('resume-qa', help='Recheck exact content and rendered artifacts')
    rq.add_argument('directory')
    final = sub.add_parser('resume-finalize', help='Release only after factual, content and visual checks pass')
    final.add_argument('directory')
    final.add_argument('--content-review')
    final.add_argument('--visual-review')
    compare = sub.add_parser('resume-compare', help='Select the cleanest reviewed revision of one JD')
    compare.add_argument('directories', nargs='+')
    args = p.parse_args()
    with (ROOT / 'data/.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command.startswith('resume'):
            from src.resume import pipeline
            try:
                if args.command == 'resume':
                    print(pipeline.build(args.jd,args.lane,args.label,args.proposal,args.plan_only))
                elif args.command == 'resume-revise':
                    directory=Path(args.directory)
                    print(pipeline.build(directory/'job_description.txt',label='revision',proposal=args.proposal,previous=directory))
                elif args.command == 'resume-qa':
                    *_, findings=pipeline.check_build(args.directory)
                    print(json.dumps(findings,indent=2))
                elif args.command == 'resume-finalize':
                    print(pipeline.finalize(args.directory,args.content_review,args.visual_review))
                elif args.command == 'resume-compare':
                    print(pipeline.compare_builds(args.directories))
            except (ValueError,RuntimeError,KeyError,FileNotFoundError) as error:
                p.error(str(error))
        elif args.command == 'review-import':
            result = import_reviewed(load(ROOT / 'data/jobs.json', []), load(args.file), load(ROOT / 'data/lanes.json'))
            atomic_json(ROOT / 'data/jobs.json', result)
            path, _ = render()
            print('Reviewed jobs imported; dashboard rebuilt:', path)
        elif args.command == 'feedback':
            result = merge_feedback(load(ROOT / 'data/feedback.json', {}), load(args.file), load(ROOT / 'data/jobs.json'))
            atomic_json(ROOT / 'data/feedback.json', result)
            path, _ = render()
            print('Feedback imported; dashboard rebuilt:', path)
        elif args.command == 'build':
            path, data = render(output=args.output)
            print(path)
            print(str(len(data['jobs'])) + ' reviewed records; ' + str(len(data['today_ids'])) + ' priority applications')
        elif args.command == 'queries':
            from src.discovery import query_plan
            path = ROOT / 'output/search_plan.json'
            atomic_json(path, query_plan(load(ROOT / 'data/lanes.json')))
            print(path)
        elif args.command == 'discover':
            from src.discovery import discover
            result = discover(load(args.boards), load(ROOT / 'data/lanes.json'))
            stamp = result['run_at'].replace(':', '').replace('+', '_')
            path = ROOT / 'output' / ('discovery_' + stamp + '.json')
            atomic_json(path, result)
            print(path)
            print(str(len(result['candidates'])) + ' candidates awaiting review; ' + str(len(result['sources'])) + ' source results recorded')


if __name__ == '__main__':
    main()
