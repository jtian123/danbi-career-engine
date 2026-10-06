"""`career.py doctor` — check this Mac has what the engine needs, in plain words."""
from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

from . import paths


def _soffice():
    for p in ('/Applications/LibreOffice.app/Contents/MacOS/soffice', 'soffice', 'libreoffice'):
        if p.startswith('/') and Path(p).exists():
            return p
        if not p.startswith('/') and shutil.which(p):
            return shutil.which(p)
    return None


def run() -> int:
    ok = True

    def line(good, what, fix=''):
        nonlocal ok
        ok &= bool(good) or not fix.startswith('REQUIRED')
        print(('  ✓ ' if good else '  ✗ ') + what + ('' if good or not fix else f'\n      → {fix}'))

    print('Python')
    line(sys.version_info >= (3, 8), f'Python {sys.version.split()[0]} ({sys.executable})', 'REQUIRED: Python 3.8 or newer')
    print('Your private data (data/)')
    for f, why in (('profile.json', 'discovery profile'), ('master_profile.json', 'résumé facts'),
                   ('resume/evidence.json', 'résumé evidence')):
        line(paths.data(f).exists(), f'data/{f} — {why}', 'REQUIRED: unzip danbi-private-data.zip into the engine folder (SETUP_FOR_CLAUDE.md step 3)')
    print('Job search + Career Hub (standard library only)')
    line(True, 'nothing extra to install')
    print('Résumé rendering (optional until you build a résumé)')
    for mod, pkg in (('docx', 'python-docx'), ('pypdf', 'pypdf'), ('pdfplumber', 'pdfplumber'), ('pypdfium2', 'pypdfium2')):
        try:
            importlib.import_module(mod)
            line(True, pkg)
        except ImportError:
            line(False, pkg, 'python3 -m pip install --user -r requirements.txt')
    so = _soffice()
    line(so, f'LibreOffice ({so or "not found"}) — turns the Word file into a PDF without popups',
         'brew install --cask libreoffice   (or download from libreoffice.org)')
    print('Hub service')
    plist = Path.home() / 'Library/LaunchAgents/com.danbi.careerhub.plist'
    line(plist.exists(), 'launchd agent installed', 'python3 career.py hub install')
    home = Path.home()
    risky = [d for d in ('Documents', 'Desktop', 'Downloads') if str(paths.ROOT).startswith(str(home / d))]
    line(not risky, f'engine folder is {paths.ROOT}',
         'move it out of ~/' + (risky[0] if risky else '') + ' (macOS blocks background services there)')
    print('\nAll required pieces are in place.' if ok else '\nFix the items marked REQUIRED above.')
    return 0 if ok else 1
