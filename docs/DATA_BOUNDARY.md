# Public and private data

Included: engine source, review prompts, dashboard template, public ATS board metadata, role-search vocabulary, curriculum metadata, tests and explicitly synthetic examples.

Excluded: personal contacts, residency details, prior resumes, academic files, private startup extracts and metrics, factual material banks, original full employer job descriptions, feedback, application records, source snapshots, generated resumes, credentials, local machine paths and Git history from other projects.

The public fixture retains stable schema IDs and a few fixed dates needed by regression tests. That compatibility does not turn synthetic prose or test states into real evidence. Example job flags such as `reviewed` and `ats_live` exist solely to test code paths; example.com URLs have no application role.

Keep real applicant data in a private workspace. Ignoring generated output does not protect edits to tracked fixture JSON files: do not push real profile replacements into this public repository.
