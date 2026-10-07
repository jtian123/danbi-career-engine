# CLAUDE.md

The working agreement for this repo is **[AGENTS.md](./AGENTS.md)** — read it fully before running
a scan, editing code, or writing a résumé. Do not fork its rules here.

Quick map:
- "Find me internships today" → AGENTS.md §3 (scan → review with `prompts/job_review.md` → mark).
  Answer in chat with a short summary; the list itself lives in the Career Hub calendar.
- Internships at universities → same pipeline, own tag and day switch; AGENTS.md §1.6.
- The Career Hub: http://127.0.0.1:7768 (`python3 career.py hub status` if it doesn't open).
- Résumés → AGENTS.md §6 and `docs/RESUME_WORKFLOW.md`.
- First time on this Mac → `SETUP_FOR_CLAUDE.md`.
- `data/` is private (her facts, the hub database). Never commit it, paste it into a public place,
  or send it anywhere. `git pull` is the only git command needed on her machine.

When Danbi states a lasting preference, record it in AGENTS.md §1 (and in your own memory), so the
next session knows it too.
