# Résumé pipeline

Her personal résumé facts and rules are private: `data/master_profile.json` (the only claim
authority), `data/resume/evidence.json` (source ledger), `data/private_docs/RESUME_WORKFLOW.md` (her
specifics: anchors, titles, open dates) and `data/ABOUT_ME.md`. Read those first. Public clones run
the same code on synthetic fixtures in `examples/data/`.

1. Save the target job description (`jobs_in/company_role.txt`, or the scan's `jds/` file).
2. `python3 career.py resume jobs_in/x.txt --lane LANE --label company_role --plan-only` — a source
   packet and a starter selection (`writer_packet.json` includes `plain_language_lint`).
3. Write the selection plan per `prompts/resume_writer.md`; build with `--proposal plan.json`.
   The compiler accepts only known claim IDs, titles and skills.
4. Rendering: python-docx → Word file → **LibreOffice headless** → PDF → page image (pypdfium2). No
   Microsoft Word, no file-access popups. Each render uses a fresh folder. The fit loop only trims
   (concise variants, then lower-priority content); it never shrinks type.
5. Audit per `prompts/resume_auditor.md`; revise with `resume-revise` (earlier builds stay); compare
   revisions with `resume-compare`.
6. Visual review per `prompts/resume_visual_review.md` (one page, ~90–95% filled).
7. `resume-finalize` creates `resume.docx` / `resume.pdf` only when content and visual reviews match
   the exact current hashes.

Lanes: product, commerce, insights, business, demand, finance, data. A strategy job uses `business`;
operations/supply chain uses `demand`.

Setup: `python3 -m pip install --user -r requirements.txt` and LibreOffice
(`brew install --cask libreoffice`). `python3 career.py doctor` checks both.
