# Danbi's career engine — working agreement

This engine serves **Danbi Jang**, a graduate student looking for internships. Who she is, her
eligibility and what she wants are **private** and live in `data/ABOUT_ME.md` (plus
`data/profile.json`) — read them before anything else. Nothing personal belongs in tracked files.

It has three parts: **discovery** (find internships, every day she asks), the **Career Hub** (a
local dashboard that stays running on her Mac), and **résumés** (source-backed, one page). Read this
file fully before changing code, running a scan, or writing a résumé. If a rule here changes, change
it here — this is the one rulebook every assistant reads (personal preferences go in
`data/ABOUT_ME.md`).

## 1. Search principles (2026-10-06)
1. **Internships first, and discovery of industries at the same time.** Don't narrow the search to
   the directions we guessed. Every US internship that is not plainly out of scope is kept; ones that
   fit no named direction land in "Other directions to explore". Her ratings teach the ordering.
2. **Eligibility follows `data/profile.json` → `work_authorization`.** The scanner never filters on
   sponsorship language. Roles that require US citizenship or a security clearance are dropped unless
   the profile says `us_citizen: true`. ITAR "U.S. person" roles are never dropped (that term covers
   permanent residents as well as citizens).
3. **Her student status is an advantage — use it.** Campus recruiting, Handshake, fairs, info
   sessions, career advising, alumni (Trojan Network, LinkedIn alumni search), clubs, treks. These
   live in `registry/campus.json` and the hub's USC tab; every job card links to USC alumni at that
   company.
4. **Any US location; any company type, but big enterprises first.** Location never affects a
   score. Big employers get an explicit, visible priority bonus (+8 enterprise, +5 large) on top of
   the reviewed fit — never hidden inside the fit.
5. **The hub is the home base.** Everything found lands in the hub's calendar on the day it was
   found, as one list for that day. No separate HTML reports.

## 2. Sources of truth (strictest wins on privacy)
| What | Where | Tracked in git? |
|---|---|---|
| Résumé facts (claims, variants, titles, dates) | `data/master_profile.json` + `data/resume/evidence.json` | **No — private** |
| Who she is, what she wants | `data/ABOUT_ME.md` | **No — private** |
| Discovery profile (eligibility, preferences) | `data/profile.json` | **No — private** |
| Hub database (jobs, her ratings, statuses, history) | `data/hub/hub.db` | **No — private** |
| Scanner memory (seen jobs, board health, LinkedIn rotation) | `data/state/` | **No — private** |
| Directions, industries, employer registry, ATS boards, USC resources, résumé layout | `registry/` | Yes (public) |
| Synthetic fixtures for tests | `examples/data/` | Yes (public) |

A job description shapes **emphasis only**; it never adds a fact. Interest is not proof of skill.
Never import James's experience, credentials, preferences, application history, or databases. (The
public ATS token list in `registry/boards.json` was seeded from his scanner's endpoint names only.)

## 3. Daily discovery recipe ("find me internships today")
1. `python3 career.py scan` — reads ~1,400 public career sources (≈3–5 min). Writes
   `output/scans/scan-DATE/` with `queue.json` (the ~40 to review), `rest.json` (everything else that
   passed the filters), `jds/` (full posting text), `review_template.json`, `summary.json`.
2. Review the queue exactly as `prompts/job_review.md` says: read every posting, fill the template,
   save `reviewed.json`. For 40 jobs, split into 4 parallel reviewers of 10 and merge.
3. `python3 career.py mark output/scans/scan-DATE` — validates every review (refuses TODOs, future
   dates, unknown lanes), puts the day's list in the hub, remembers what was shown.
4. Tell her in chat: counts by group (apply first / check first), the top 3–5 by name, deadlines this
   week, any source warnings, and anything campus-related this week. Don't paste a second list —
   the hub calendar is the list.

Details, lessons learned and the do-not list: `docs/DAILY_RUN.md`.

## 4. Discovery rules
- **Sources:** big employers' own career APIs (registry/enterprises.json — ~190 employers on Workday,
  Oracle HCM, Eightfold, Phenom, iCIMS/Jibe, Greenhouse, Lever, SmartRecruiters, JobScore, plus
  TikTok/ByteDance, Amazon, Microsoft, Apple, Google), ~1,250 mid-size and
  growth company boards (registry/boards.json), the SimplifyJobs internship list on GitHub, and
  LinkedIn's public guest search with the internship filter.
- **Never** log in, scrape Handshake, bypass a block, or retry a 403/429/999. LinkedIn: ≤ 20 requests
  per scan, 2 s apart; any block skips LinkedIn for the rest of the day.
- Apply links go to the **employer's** posting whenever one exists; LinkedIn/Simplify copies are
  discovery only.
- Dates come from first-publish fields, never `updated_at`. Seasons already over are dropped.
- Every drop is counted in `summary.json` (no silent drops). Unreviewed candidates still enter the hub
  as "found but not reviewed" with an automatic pre-rank.
- Out of scope (titles): software/hardware/other engineering disciplines (industrial engineering,
  process improvement, data/analytics engineering and engineering program management stay in),
  PhD research, lab/clinical science, IT/security, law, quant trading/actuarial, non-professional
  roles. Data science stays in as a **stretch**.
- A job she rated "not for me", dismissed, or acted on is never queued again. A job shown in the
  last 45 days is not re-queued.

## 5. The Career Hub
- `python3 career.py hub install` once: a launchd agent (`com.danbi.careerhub`) keeps it running at
  http://127.0.0.1:7768, starts at login, restarts within ~30 s. `Career Hub.command` reopens it.
  Only answers on 127.0.0.1. The page is read fresh on every request (no restart after UI edits);
  Python changes need `launchctl kickstart -k gui/$(id -u)/com.danbi.careerhub`.
- She sets statuses and ratings herself (no inbox sync). Every change is kept in `events`.
- **Priority = reviewed fit (0–100) + big-employer bonus + learned preference (±10).** Fit =
  transfer /30 + learning /25 + readiness /20 + pay /10 + conversion /10 + timing /5. It is an
  ordering, never an acceptance probability.
- **Learning from her ratings:** per direction and per industry, `10 × Σvotes / (3 + n)`, capped ±10.
  Interested = 1, curious = 0.4, not for me = −1 — but "not for me" counts against a direction only
  when the reason is *the work itself*, and against an industry only when the reason is *the industry*.
  Pay/timing/location dislikes say nothing about the career.
- To read what she has done: `python3 career.py prefs`, or query `data/hub/hub.db` (read-only).

## 6. Résumé rules
Read `docs/RESUME_WORKFLOW.md`, `prompts/resume_writer.md`, `data/master_profile.json`,
`data/resume/evidence.json` before résumé work.
1. **Truth first.** Only claims in `data/master_profile.json` (the compiler accepts claim IDs, not
   prose). New wording goes into the profile as a source-backed variant first. Honest gaps stay
   gaps; don't hedge what she has actually done either.
2. **Attribution:** PM, design, marketing and other non-technical Apateu/XResearch work is hers
   (user's explicit scope); engineering implementation is not. Never mention James or who did what.
3. **Apateu leads** and always precedes XResearch; XResearch is optional, ≤ 2 bullets. Co-founder
   titles are allowed for both. James's personal title rules do NOT apply to her.
4. **2–3 experiences, weighted:** the lead role has the most bullets; taper the rest.
5. **One page, ~90–95% filled.** The fit loop only trims; an underfull page goes back to the writer
   for one more strong claim — never padding, never bigger type. Look at the rendered page.
6. **Plain language (说人话):** see the section in `prompts/resume_writer.md`. QA prints warnings for
   long bullets, spec lists, in-house names, filler words, arrow chains, status words, third person.
7. **Years of experience** must describe the right kind of work (e-commerce marketing ≠ analytics).
8. **Links:** her LinkedIn and whole product names (Apateu → https://www.app.westapt.com/); never a
   raw URL inside a bullet; no `[Name]` residue.
9. **Education near the top**; two or three relevant courses per `docs/COURSEWORK_STRATEGY.md`
   (program curriculum ≠ completed coursework).
10. Open dates listed in `data/ABOUT_ME.md` must be confirmed before a résumé is called
    submission-ready. Unknown dates never become "Present".
11. Writer → audit → revise → deterministic QA → real PDF (LibreOffice) → final re-audit → visual
    review → `resume-finalize`. Delivered résumés are never edited retroactively; new rules apply
    to future builds.

## 7. Commands
```sh
python3 career.py doctor                      # what this Mac still needs
python3 career.py scan [--queue 40] [--no-linkedin] [--no-boards]
python3 career.py mark output/scans/scan-DATE [--reviewed FILE]
python3 career.py hub install|status|open|serve|uninstall
python3 career.py lead "Company" "Title" --url URL --source handshake --deadline 2026-11-01
python3 career.py status JOB_ID applied        # same as the hub's status menu
python3 career.py never "Company"              # never show again
python3 career.py prefs                        # what her ratings say
python3 career.py campus-sync                  # load registry/campus.json events into the hub
python3 career.py resume jobs_in/x.txt --lane product --label company_role [--plan-only|--proposal plan.json]
python3 career.py resume-qa|resume-finalize|resume-compare|resume-revise ...
python3 -m unittest discover -s tests          # after any behavior change
```
Lanes (directions): commerce, insights, product, strategy, business, demand, operations, finance,
data, explore. The résumé `--lane` uses the original seven (product, commerce, insights, business,
demand, finance, data — each has a headline and summary in the profile): for a strategy/consulting
job use `business`; for operations/supply chain use `demand`; for "explore" pick the closest.

## 8. Keeping this current
- When she states a durable preference ("no more insurance", "I like supply chain"), write it in
  `data/ABOUT_ME.md` (and, if it is a ranking rule, into the code/registry) — not only in chat.
- Refresh `registry/campus.json` monthly (dated events, fair dates — spring 2027 fair dates were not
  published as of 2026-10-06) and run `campus-sync`.
- Add big employers she cares about to `registry/enterprises.json` (verify the endpoint live first).
