"""Keep the Career Hub running on her Mac (launchd), like James's JobHub.

  install    write ~/Library/LaunchAgents/com.danbi.careerhub.plist, start it, open the page.
             Starts at login and restarts within ~30 s if it ever stops.
  uninstall  stop it and remove the agent.
  status     is it loaded, is the port answering, last log lines.
  open       open the page.
  serve      run in this terminal (what launchd runs).

launchd is the only owner of the port; `Career Hub.command` kickstarts the agent
instead of starting a second copy.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .. import paths

LABEL = 'com.danbi.careerhub'
PLIST = Path.home() / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'
LOG = Path.home() / 'Library' / 'Logs' / 'danbi-career-hub.log'


def _up(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def _plist(port):
    py = sys.executable or '/usr/bin/python3'
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LABEL}</string>
    <!-- Danbi's Career Hub: http://127.0.0.1:{port} -->
    <key>ProgramArguments</key>
    <array>
        <string>{py}</string>
        <string>{paths.ROOT / 'career.py'}</string>
        <string>hub</string><string>serve</string><string>--port</string><string>{port}</string>
    </array>
    <key>WorkingDirectory</key><string>{paths.ROOT}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>{LOG}</string>
    <key>StandardErrorPath</key><string>{LOG}</string>
    <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
'''


def _launchctl(*args):
    return subprocess.run(['launchctl', *args], capture_output=True, text=True)


def _domain():
    return f'gui/{os.getuid()}'


def run(action: str, port: int = 7768) -> int:
    if action == 'serve':
        from .server import serve
        return serve(port)
    if action == 'open':
        subprocess.run(['open', f'http://127.0.0.1:{port}/'])
        return 0
    if sys.platform != 'darwin' and action in ('install', 'uninstall'):
        print('launchd is macOS-only. Run `python3 career.py hub serve` instead.')
        return 1
    if action == 'install':
        home = Path.home()
        for protected in ('Documents', 'Desktop', 'Downloads'):
            if str(paths.ROOT).startswith(str(home / protected)):
                print(f'WARNING: the engine lives under ~/{protected}. macOS blocks background services from '
                      f'reading that folder, so the hub may fail to start. Move the folder to your home '
                      f'directory (e.g. ~/danbi-career-engine) and run install again.')
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        _launchctl('bootout', f'{_domain()}/{LABEL}')
        PLIST.write_text(_plist(port))
        r = _launchctl('bootstrap', _domain(), str(PLIST))
        if r.returncode not in (0, 5, 37):   # 5/37: already loaded
            print('launchctl bootstrap said:', r.stderr.strip() or r.stdout.strip())
        for _ in range(40):
            if _up(port):
                break
            time.sleep(0.5)
        if _up(port):
            print(f'Career Hub is running at http://127.0.0.1:{port} and will start at every login.')
            subprocess.run(['open', f'http://127.0.0.1:{port}/'])
            return 0
        print(f'The hub did not answer yet. Check the log: tail -50 "{LOG}"')
        return 1
    if action == 'uninstall':
        _launchctl('bootout', f'{_domain()}/{LABEL}')
        if PLIST.exists():
            PLIST.unlink()
        print('Career Hub service removed (your data in data/ is untouched).')
        return 0
    if action == 'status':
        loaded = _launchctl('print', f'{_domain()}/{LABEL}').returncode == 0
        print(f'launchd agent: {"loaded" if loaded else "not installed"} ({PLIST})')
        print(f'port {port}: {"answering" if _up(port) else "not answering"}')
        if LOG.exists():
            print('--- last log lines ---')
            print('\n'.join(LOG.read_text(errors='replace').splitlines()[-8:]))
        return 0
    return 1
