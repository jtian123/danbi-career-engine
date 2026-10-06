#!/usr/bin/env python3
"""Mirror the PUBLIC part of this workspace into public-release/ (the GitHub checkout).

Since 2026-10-06 the code layout is identical in both places: everything personal lives in
data/ and output/ (git-ignored), so the export is an allowlist copy — no synthetic rewrites.
It refuses to run if a tracked-to-be file contains a private marker (contacts, residency
words, local paths). Review `git -C public-release status` before any commit or push.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'public-release'
INCLUDE = ['career.py', 'AGENTS.md', 'CLAUDE.md', 'README.md', 'SETUP_FOR_CLAUDE.md', 'Career Hub.command',
           'requirements.txt', '.gitignore', 'registry', 'src', 'prompts', 'docs', 'examples', 'templates', 'tests',
           'scripts/sync_public.py', 'jobs_in/example_product_intern.txt', 'jobs_in/example_growth_intern.txt']
SKIP = re.compile(r'__pycache__|\.pyc$|\.DS_Store$')
# Generic markers that must never appear in a public file. Her own contact details are read
# from the PRIVATE profile at run time, so no personal data is ever written into this script.
GENERIC = [r'/Users/[a-z]', r'green card', r'ABOUT_ME\.md content']


def private_markers():
    import json
    pats = list(GENERIC)
    try:
        prof = json.loads((ROOT / 'data' / 'master_profile.json').read_text())
        contact = prof.get('identity', {}).get('contact', {}) or prof.get('contact', {})
        for key in ('email', 'phone', 'linkedin'):
            val = str(contact.get(key) or '').strip()
            if len(val) >= 6:
                core = re.sub(r'^https?://(www\.)?', '', val)
                digits = re.sub(r'\D', '', core)
                pats.append(re.escape(core) if key != 'phone' else r'[-. ()]*'.join(digits[-7:]))
    except (OSError, ValueError):
        pass
    return pats


def files():
    for item in INCLUDE:
        p = ROOT / item
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob('*')):
                if f.is_file() and not SKIP.search(str(f)):
                    yield f


def main():
    if not (OUT / '.git').exists():
        sys.exit('public-release/ is not a git checkout')
    wanted = {f.relative_to(ROOT) for f in files()}
    bad = []
    for rel in sorted(wanted):
        if rel.name == 'sync_public.py':   # this file lists the patterns themselves
            continue
        text = (ROOT / rel).read_text(errors='ignore') if (ROOT / rel).suffix in ('.py', '.md', '.json', '.html', '.txt', '.command', '') else ''
        for pat in private_markers():
            if re.search(pat, text, re.I):
                bad.append(f'{rel}: matches {pat!r}')
    if bad:
        sys.exit('Refusing to export; private markers found:\n  ' + '\n  '.join(bad))
    # remove previously exported files that are no longer public
    import subprocess
    tracked = subprocess.run(['git', '-C', str(OUT), 'ls-files'], capture_output=True, text=True).stdout.split('\n')
    for t in filter(None, tracked):
        if Path(t) not in wanted:
            (OUT / t).unlink(missing_ok=True) if sys.version_info >= (3, 8) else None
    for rel in wanted:
        dst = OUT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
    print(f'{len(wanted)} public files mirrored into {OUT}. Now: git -C public-release status / diff, run the '
          f'tests there, then commit.')


if __name__ == '__main__':
    main()
