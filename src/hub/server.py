"""Career Hub local server — standard library only, binds 127.0.0.1 (never expose it).

  python3 career.py hub serve [--port 7768]

The page (page.html) is read on every request, so edits show on reload without a restart.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from .. import paths
from . import db

DEFAULT_PORT = 7768
PAGE = paths.ROOT / 'src' / 'hub' / 'page.html'


def _registry(name, default):
    p = paths.registry(name)
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def state() -> dict:
    st = db.full_state()
    lanes = _registry('lanes.json', {'lanes': []})
    st['lanes'] = lanes['lanes'] if isinstance(lanes, dict) else lanes
    st['industries'] = _registry('industries.json', {'industries': []})['industries']
    st['campus'] = _registry('campus.json', {'resources': [], 'events': []})
    ent = _registry('enterprises.json', {'employers': []})
    st['enterprises'] = [{'name': e['name'], 'industry': e.get('industry'), 'tier': e.get('tier'),
                          'korean': e.get('korean'), 'usc': e.get('usc'), 'direct': bool(e.get('ats'))}
                         for e in ent['employers']]
    prof = paths.data('profile.json')
    try:
        p = json.loads(prof.read_text())
        st['profile'] = {'name': p['identity']['name'], 'graduation': (p.get('education') or [{}])[-1].get('expected_graduation')}
    except (OSError, ValueError, KeyError):
        st['profile'] = {'name': 'Danbi', 'graduation': None}
    return st


class Handler(BaseHTTPRequestHandler):
    server_version = 'CareerHub/1.0'

    def _send(self, code, body: bytes, ctype='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', ctype + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def log_message(self, fmt, *args):
        if args and str(args[1] if len(args) > 1 else '').startswith(('4', '5')):
            sys.stderr.write('[hub] ' + (fmt % args) + '\n')

    def _file_under(self, base, rel, ctype):
        full = (base / unquote(rel)).resolve()
        if not str(full).startswith(str(base.resolve()) + '/') or not full.is_file():
            return self._send(404, b'{"error":"not found"}')
        self._send(200, full.read_bytes(), ctype)

    def do_GET(self):
        try:
            u = urlparse(self.path)
            if u.path in ('/', '/index.html'):
                self._send(200, PAGE.read_bytes(), 'text/html')
            elif u.path == '/api/state':
                self._json(state())
            elif u.path.startswith('/jd/'):
                self._file_under(paths.OUTPUT / 'scans', u.path[4:], 'text/plain')
            elif u.path.startswith('/resume/'):
                rel = u.path[8:]
                ctype = 'application/pdf' if rel.endswith('.pdf') else 'application/octet-stream'
                self._file_under(paths.OUTPUT / 'resumes', rel, ctype)
            elif u.path == '/health':
                self._json({'ok': True})
            else:
                self._send(404, b'{"error":"not found"}')
        except Exception as e:  # noqa: BLE001 — surface, never crash the server
            self._json({'error': f'{type(e).__name__}: {e}'}, 500)

    def do_POST(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if not 0 <= n <= 200_000:
                return self._json({'error': 'bad length'}, 400)
            body = json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            return self._json({'error': 'bad JSON'}, 400)
        try:
            if self.path == '/api/status':
                self._json({'ok': True, 'job': db.set_status(int(body['id']), body['status'], str(body.get('note') or ''))})
            elif self.path == '/api/rate':
                self._json({'ok': True, 'job': db.rate(int(body['id']), body.get('interest', ''), body.get('reason', ''),
                                                       body.get('confidence', ''), body.get('notes'))})
            elif self.path == '/api/lead':
                self._json({'ok': True, 'job': db.add_lead(body.get('company', ''), body.get('title', ''),
                                                           body.get('url', ''), body.get('source', 'handshake'),
                                                           body.get('location', ''), body.get('notes', ''),
                                                           body.get('deadline', ''))})
            elif self.path == '/api/event':
                self._json({'ok': True, 'id': db.add_campus_event(body.get('title', ''), body.get('date', ''),
                                                                  body.get('url', ''), body.get('kind', 'event'),
                                                                  body.get('description', ''), body.get('time', ''))})
            elif self.path == '/api/event-done':
                db.mark_campus_done(int(body['id']), bool(body.get('done', True)))
                self._json({'ok': True})
            elif self.path == '/api/pay-floor':
                year, hour = int(body.get('year', 70000)), float(body.get('hour', 22))
                if not (0 <= year <= 500000 and 0 <= hour <= 200):
                    return self._json({'error': 'pay floor out of range'}, 400)
                db.set_setting('uni_pay_floor', {'year': year, 'hour': hour})
                self._json({'ok': True, 'pay_floor': db.pay_floors()})
            elif self.path == '/api/never':
                self._json({'ok': True, 'added': db.never_add(body['company'])})
            else:
                self._json({'error': 'not found'}, 404)
        except (KeyError, ValueError) as e:
            self._json({'error': str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            self._json({'error': f'{type(e).__name__}: {e}'}, 500)


def serve(port: int = DEFAULT_PORT) -> int:
    try:
        campus = json.loads(paths.registry('campus.json').read_text())
        db.sync_campus(campus)
    except (OSError, ValueError):
        pass
    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'Career Hub → http://127.0.0.1:{port}   (Ctrl-C to stop)', flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nCareer Hub stopped.')
    return 0
