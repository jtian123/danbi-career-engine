# Public career engine demonstration

This repository is the public code and methodology distribution. Every supplied applicant record, resume claim, employer and job is synthetic test data. The name and stable IDs are compatibility fixtures, not a factual resume. Do not treat fixtures as personal achievements or live opportunities.

Keep real contacts, immigration information, transcripts, prior resumes, private company evidence, feedback, application state and generated resume packets out of public Git history. Use a private clone/workspace for real applicant data. Never assume a catalog course was taken. Employer verification and eligibility precede ranking. A current assistant performs editorial passes; the code compiles, validates and renders.

Run `python3 -m unittest discover -s tests` after behavioral changes. Inspect rendered HTML after UI changes. The public examples intentionally include an unresolved date to exercise the release gate.
