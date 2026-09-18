# Danbi Career Engine — public methodology and code

An internship discovery and resume-building engine for exploring business-facing analytical and product careers while in graduate school.

**This public distribution uses synthetic data.** Applicant contacts, resume claims, employer records, job descriptions, comparison scores and availability states are fictional examples, not Danbi's resume or a live job shortlist. The runtime retains the name Danbi Jang and stable schema IDs for compatibility. Real applicant records and private source evidence remain outside this repository.

## Start locally

Python 3.8+ on macOS or Linux is enough for these offline commands (the CLI uses POSIX file locking):

```sh
python3 -m unittest discover -s tests
python3 career.py build
python3 career.py queries
python3 career.py resume jobs_in/example_product_intern.txt --lane product --plan-only
```

`build` creates a standalone HTML dashboard in `output/`; it does not refresh or verify job availability. The dashboard shows a prominent synthetic-demo label. Fixed-date test jobs become stale by design, so running later may show no priority items. `queries` produces a research plan, not search results. `--plan-only` creates a draft selection and review packet without document-rendering dependencies.

Read [the pipeline guide](docs/pipeline-guide.html), [resume workflow](docs/RESUME_WORKFLOW.md), [coursework strategy](docs/COURSEWORK_STRATEGY.md) and [public/private data boundary](docs/DATA_BOUNDARY.md). Download the HTML guide and open it in a browser; GitHub's source view does not render HTML as a webpage.

## Discovery and review

Search across seven career directions and immediate, academic-year and summer opportunities. Public ATS adapters support Greenhouse, Lever, Ashby and Workday. The included board metadata contains public board identifiers, not credentials.

```sh
python3 career.py discover
```

This command makes network requests to public employer boards and writes an **unreviewed** queue. A person or assistant must read each full employer posting, verify eligibility and actual application availability, and record evidence before importing reviewed records. Access blocks are recorded; they are not bypassed.

Student platforms such as Handshake and LinkedIn are discovery surfaces for manual/account-authorized research. This project does not implement authenticated scraping or automatic applications. Employer postings remain the verification source for a recommendation.

Ranking follows eligibility and freshness gates. Scores explain an ordering, not interview probability. Feedback changes preferences only; it cannot create skills or eligibility. Applied jobs remain recorded and leave the daily shortlist.

## Resume method and optional rendering

The current assistant supplies the writing, source/editorial audit and revision passes. Code compiles approved claim IDs, preserves skill/course status, measures rendered PDFs and requires content plus visual reviews before release. There is no independent model call or borrowed API key.

The `--plan-only` path works in an ordinary clone. Full Word/PDF rendering currently expects the Codex bundled document runtime at its standard per-user cache location, including Python, LibreOffice and the document renderer. It is **not** a portable standalone renderer installation. `requirements-render.txt` lists optional Python packages for inspection; installing that file alone does not provide the required renderer or LibreOffice. Users outside that runtime can use plan-only output or explicitly integrate their own compatible renderer.

The synthetic sample intentionally leaves one employment end date unknown. Release correctly remains blocked; do not fill a date merely to make a demo pass. Never submit generated demo content.

## Tests

The 53 Python tests cover eligibility, stale/closed jobs, identity, deduplication, feedback, public ATS parsing, source-bound resume claims, unknown dates, curriculum status and release gates. They use fixed-date synthetic fixtures and mocked network responses. Historical browser tests tied to private data were excluded from this distribution; inspect the generated dashboard when changing the interface.

Generated output, feedback and secrets are ignored. Real applicant data belongs in a private workspace and must never replace tracked public demo files in a pushed commit. No software license has been selected.

Optional guide browser checks use Node.js, Playwright and Chrome: `node tests/guide.spec.cjs`. They validate filtering, keyboard navigation, course selection, mobile width, print and no-JavaScript fallback. Set `PLAYWRIGHT_MODULE` when using a bundled module path.
