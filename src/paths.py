"""Where things live.

registry/  public, tracked: directions, industries, employer registry, ATS boards,
           search titles, USC resources, résumé layout config.
data/      PRIVATE, never tracked: Danbi's profile, résumé facts and evidence, the
           hub database and the scanner's memory. On a fresh clone it is empty
           until her private bundle is unzipped into it (see SETUP_FOR_CLAUDE.md).
output/    generated scans and résumés (never tracked).

DANBI_DATA overrides the data directory (tests point it at examples/data).
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / 'registry'
OUTPUT = ROOT / 'output'


def data_dir() -> Path:
    return Path(os.environ.get('DANBI_DATA') or ROOT / 'data')


def data(*parts) -> Path:
    return data_dir().joinpath(*parts)


def state(*parts) -> Path:
    p = data_dir() / 'state'
    p.mkdir(parents=True, exist_ok=True)
    return p.joinpath(*parts)


def registry(name: str) -> Path:
    return REGISTRY / name
