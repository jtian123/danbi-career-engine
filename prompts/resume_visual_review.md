# Résumé visual inspection

Also read `data/private_docs/resume_visual_review.md` (which links must be present for her).

Open every `pages/page-*.png` of the exact current build at full size. Check: exactly one US Letter
page; readable type; natural reading order; margins, clipping and overlap; spacing between header,
education, roles, bullets and skills; intended hyperlink destinations. The page should look balanced:
about **90–95% filled** (`artifact_qa.json` → `geometry.height_fraction`; an advisory appears outside
85–98%). An underfull page goes back to the writer for one more strong claim or a fuller variant —
never padding, never larger type. Page count and text extraction alone are not visual verification.
Bind the review to the current artifact hashes, then run `resume-finalize`.
