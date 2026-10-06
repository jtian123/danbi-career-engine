# Daily discovery playbook

The short version lives in AGENTS.md §3. This file holds the details and the lessons that cost
something to learn (most were learned first in James's scanner and carried over on 2026-10-06).

## The run
```sh
python3 career.py scan                 # ≈3–5 min; prints the funnel and any warnings
# review output/scans/scan-DATE/review_template.json → reviewed.json (prompts/job_review.md)
python3 career.py mark output/scans/scan-DATE
```
- **Review in parallel.** Split the template into four slices of ~10, give each reviewer the
  contract (`prompts/job_review.md`), her `data/profile.json` and `data/projects_experience_db.md`,
  then concatenate the results into `reviewed.json`. Spot-check two or three before marking.
- `mark` refuses incomplete reviews; fix and rerun. It is idempotent (re-marking the same scan never
  duplicates), and a second scan the same day gets its own folder (`scan-DATE-2`).
- Reviews may include a job that is not in the queue (e.g. one she or LinkedIn surfaced earlier):
  give it a `key` like `company|title words` and fill every field.
- If she asks for "more", raise `--queue` (e.g. 60) rather than scanning twice — LinkedIn's daily
  budget is per scan.

## What the funnel numbers mean
`postings read → US internships in scope → unique → new to her → queued for review`.
- *Unique*: one row per job. Copies of one program across cities collapse ("Store Executive Intern –
  Miami" and "– Des Moines" are one job; the other cities are kept in `also_locations`). The
  employer's own posting wins over a LinkedIn/Simplify copy.
- *New to her*: not shown in the last 45 days, not dismissed, not rated "not for me", not acted on.
- Every dropped posting is counted by reason in `summary.json → dropped`.

## Pre-rank (only decides what Claude reads first)
employer size 25/18/10/8 · direction 25 (core) / 20 (finance, data) / 8 (explore), −8 stretch ·
degree 15 (master's allowed) / 8 (unclear) / −20 (undergrad-only) · season 10 (spring/summer 2027) ·
freshness ≤10 · pay listed 3 · USC connection 4 · her edge (Korean +5, beauty/e-commerce +3) ·
her ratings ±10. Queue: ≤2 per employer, no direction over 40%, the last ~5 slots go to directions
or industries not yet in the queue (exploration).

## Source notes
- **Workday** (most big employers): the scanner asks each tenant for its own "Intern" job type
  facet first — far more precise than keyword search, which on some tenants returns hundreds of
  unrelated jobs. Pharmacy/medical intern facets are skipped. Dates are relative ("Posted 3 Days
  Ago").
- **Greenhouse** gives `company_name`, `first_published` and sometimes `application_deadline`.
  **Ashby** marks `employmentType: Intern` even when the title doesn't say intern.
- **SimplifyJobs** (`SimplifyJobs/Summer2027-Internships` on GitHub) is community-maintained and
  tech-leaning (data, product, software). Its links are usually the employer's own. Its degree tags
  are a hint, not proof.
- **LinkedIn guest search** with `f_E=1` (internship) — the best source for business/marketing
  internships at non-tech companies. ≤ 20 requests per scan; the query window rotates through all
  directions' queries (`data/state/linkedin.json`), ranked by how many distinct companies each query
  has found. A block (403/429/999 or an auth wall) is logged and LinkedIn is skipped for the day.
- **Handshake** needs her USC login: she browses it herself and adds finds with the hub's "Add a job
  you found yourself" form (or `career.py lead`). Never automate it.
- Board health is tracked per source; a source that fails 4 times in a row goes dormant and is
  retried on probation (7, 14 … 30 days).

## Do-not list
- Never log in anywhere, solve a CAPTCHA, rotate identities, or retry a block.
- Never use `updated_at` as a posting date (re-saves look new).
- Never trust a guessed ATS token without checking the board's own company name (a guessed
  `greenhouse:elite` once belonged to a physical-therapy clinic).
- SmartRecruiters answers 200 with an empty list for ANY company slug — an empty answer proves
  nothing.
- Don't run more than ~16 parallel workers: the macOS DNS resolver starts failing mid-run (the
  client retries a DNS failure once).
- Search-result URLs are not identity; a requisition URL is.
- Name a run by the LOCAL date (an evening scan belongs to today, not tomorrow in UTC).
- A closed posting is a `skip` with a reason — never a score of 0.
- Eligibility comes from `data/profile.json` → `work_authorization`: never filter on sponsorship
  language; drop citizenship-only/clearance roles unless she is a citizen; never drop ITAR
  "U.S. person" roles on that wording alone.

## Extending
- **A big employer is missing:** find its public careers backend, verify it returns listings
  without login, add it to `registry/enterprises.json` (`tier: enterprise`, `industry`, `ats`).
  Workday tenants: POST `https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`;
  a 404 mentioning `Job_Posting_Site_ID` on site `__probe__` proves the tenant/instance.
- **A new direction keeps appearing in "explore":** add a title rule to `registry/lanes.json`
  (first match wins — order matters) and a direction card, and rerun the tests.
