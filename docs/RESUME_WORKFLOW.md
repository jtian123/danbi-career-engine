# Resume pipeline

The supplied profile and evidence are **synthetic fixtures**, not an applicant resume. The code is the same source compiler and quality-gate architecture used in the private workspace.

1. Maintain reviewed facts and wording variants in a private canonical profile, with evidence IDs and limitations.
2. Save a target job description and select relevant claims, skills and two or three curriculum courses.
3. The current assistant writes the selection plan and performs a separate source/editorial review. There is no automatic second-model or paid API call.
4. Compile only known claims and supported titles. Unknown dates stay unknown. Course availability cannot manufacture skill, project or completion claims.
5. Render Word/PDF, measure the actual PDF, fit by concise wording and lower-priority content, then re-audit the content that actually rendered.
6. Inspect every final page. Release requires clean source, editorial and visual reviews bound to exact content and artifact hashes.

The default public data intentionally keeps one synthetic employment end date unresolved, so real release is blocked. This exercises the gate without inventing an applicant fact. Public demos must never be submitted.

`python3 career.py resume jobs_in/example_product_intern.txt --lane product --plan-only` works with Python's standard library. Full document rendering requires the separate document runtime described in the README. Prompt files define the assistant's editorial responsibilities.
