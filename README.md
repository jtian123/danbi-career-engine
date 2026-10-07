# Danbi's career engine

Internship discovery, an always-on local **Career Hub**, and source-backed one-page résumés for
**Danbi Jang**, a USC graduate student. Built to be driven by Claude: she asks in plain words
("find me internships today", "build my résumé for this"), Claude runs the engine, and everything
lands in the hub on her Mac.

- **New machine?** → [SETUP_FOR_CLAUDE.md](SETUP_FOR_CLAUDE.md)
- **Rules for any assistant** → [AGENTS.md](AGENTS.md) (Claude also reads [CLAUDE.md](CLAUDE.md))
- **Daily discovery details** → [docs/DAILY_RUN.md](docs/DAILY_RUN.md)
- **Résumés** → [docs/RESUME_WORKFLOW.md](docs/RESUME_WORKFLOW.md),
  [docs/COURSEWORK_STRATEGY.md](docs/COURSEWORK_STRATEGY.md)

## How it fits together
```
public career sources ──scan──▶ output/scans/scan-DATE/ ──Claude reviews──▶ mark ──▶ Career Hub
 (big employers' own sites,      queue (≈40) + rest            prompts/job_review.md      http://127.0.0.1:7768
  ~1,300 company boards,         + full posting text                                    calendar · applications
  SimplifyJobs list,                                                                    explore · USC · companies
  LinkedIn internship search)                                   her ratings ──▶ learned preferences (±10)
```
- **Scope:** US internships in any industry; big employers first; eligibility follows her private
  profile (no sponsorship filter); software/hardware/lab roles excluded,
  data science kept as a stretch; anything else that fits no named direction is kept as "explore".
- **Internships at universities** are a dedicated field inside the same pipeline: searched for
  daily, reviewed by Claude like any internship, tagged "University" on the daily list.
- **Priority** = Claude's reviewed fit (0–100) + big-employer bonus (+8/+5) + what her ratings taught
  it (±10). An ordering, not an acceptance probability.
- **Privacy:** her facts and the hub database live in `data/`, which is never tracked. The hub only
  answers on 127.0.0.1. Nothing applies, emails or logs in for her.

## Commands
```sh
python3 career.py doctor                              # check this Mac
python3 career.py scan                                # find new internships → review queue
python3 career.py mark output/scans/scan-DATE         # reviewed list → the hub
python3 career.py hub install | status | open         # the always-on Career Hub
python3 career.py lead "Company" "Title" --url URL    # add a Handshake / fair / referral find
python3 career.py prefs                               # what her ratings say
python3 career.py resume jobs_in/job.txt --lane product --label company_role --plan-only
python3 -m unittest discover -s tests
```
Standard library only for discovery and the hub (Python 3.8+). Résumé rendering needs
`requirements.txt` and LibreOffice.

## Layout
| Path | What | Tracked |
|---|---|---|
| `src/scan/` | sources, filters, classification, memory, scan + mark | ✓ |
| `src/hub/` | SQLite layer, local server, the page, launchd service | ✓ |
| `src/resume/` | claim-ID compiler, Word renderer, LibreOffice/PDF checks | ✓ |
| `registry/` | directions, industries, big-employer registry, ATS boards, USC resources, résumé layout | ✓ |
| `prompts/` | job-review contract; résumé writer / auditor / visual review | ✓ |
| `examples/data/` | synthetic fixtures (tests run on these, never on her data) | ✓ |
| `data/` | **her private data**: profile, résumé facts, evidence, hub.db, scanner memory | ✗ |
| `output/` | scans and résumé builds | ✗ |
