"""Polite HTTP for public career endpoints. Standard library only.

One request per call; a DNS hiccup gets one retry (macOS resolvers throttle under
many workers). Access blocks (403/429/999) are raised to the caller, never retried.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/128.0 Safari/537.36')
BLOCK_CODES = (403, 429, 999)


class Blocked(Exception):
    """The site refused us. Record it and stop using that source for the run."""


def get(url, data=None, headers=None, timeout=25, raw=False):
    h = {'User-Agent': UA, 'Accept': 'text/html' if raw else 'application/json'}
    if data is not None:
        h['Content-Type'] = 'application/json'
    h.update(headers or {})
    body = json.dumps(data).encode() if data is not None else None
    for attempt in (0, 1):
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode('utf-8', 'replace')
            return text if raw else json.loads(text)
        except urllib.error.HTTPError as e:
            if e.code in BLOCK_CODES:
                raise Blocked(f'HTTP {e.code}') from e
            raise
        except urllib.error.URLError as e:
            if attempt == 0 and isinstance(getattr(e, 'reason', None), socket.gaierror):
                time.sleep(1.5)
                continue
            raise
