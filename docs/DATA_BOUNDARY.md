# Public and private data

The repository (code, `registry/`, `prompts/`, `docs/`, `examples/`, `tests/`) is public. Everything
personal lives in `data/` and `output/`, which are git-ignored and travel only in her private bundle.

Public: engine source, the hub page, review prompts, public career-site endpoints and search
vocabulary, USC resource links, curriculum metadata, synthetic fixtures, tests.

Private (`data/`, `output/`): who she is (`ABOUT_ME.md`), eligibility and residency details,
contacts, prior résumés, the résumé claim bank and evidence, her personal résumé rules
(`private_docs/`), the hub database (ratings, statuses, notes), scanner memory, saved job
descriptions, generated résumés.

Never copy a file from `data/` into a tracked path, and never write personal facts into tracked
docs. Tests run on `examples/data/` (synthetic) — set `DANBI_DATA` to point elsewhere.
