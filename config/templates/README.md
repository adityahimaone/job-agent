# Templates & candidate knowledge base

Everything in this folder is plain Markdown you can edit by hand. No code and no
AI key are needed to produce documents. See `docs/TEMPLATES.md` for the full
guide.

## Files

| File | What it is | Rendered by the engine? |
| --- | --- | --- |
| `candidate_profile.md` | **The knowledge base.** All verified facts, roles, projects, skills, links and constraints live here. This is the single source of truth. | No (parsed into a `CandidateProfile`) |
| `resume_master.md` | Master resume (Markdown / PDF source). | Yes → `resume_markdown` |
| `cover_letter.md` | Three-paragraph cover letter. | Yes → `cover_letter` |
| `email_apply.md` | Cold-application email. **First line must be `Subject: ...`**, then a blank line, then the body. | Yes → `email_subject` + `email_body` |
| `connection_note.md` | LinkedIn connection note (keep under 300 characters). | Yes → `connection_note` |
| `gig_profile.md` | Short bio for AI-training / expert networks. | Yes → `gig_profile` |

The engine always uses the file on disk. If a file is missing or contains a
mistake, it falls back to a safe built-in text rather than crashing.

## Variables available in every render template

| Variable | Meaning |
| --- | --- |
| `candidate` | The whole knowledge base: `name`, `email`, `location`, `timezone`, `headline`, `years_experience`, `years_experience_label` ("4+ years"), `links` (`portfolio`, `github`, `linkedin`), `facts`, `facts_text`, `skills` (category → list), `skills_flat`, `roles`, `projects`, `constraints`, `do_not_claim`, `education`. |
| `job` | The posting: `title`, `company`, `location`, `description`, `url`, `employment_type`, `remote_policy`, `timezone`, etc. |
| `requirements` | Extracted requirements: `must_have`, `nice_to_have`, `employment_types`, `remote_policy`, `timezone`, `seniority`, `summary`. |
| `evidence` | A list of 3–5 grounded bullet strings chosen to match the job. Use a `{% for %}` loop or `join`. |
| `matched_skills` | List of skill names. |
| `profile` | The active search-profile dict (`config/profiles/*.yaml`). |
| `greeting` | "Dear <company> hiring team," |
| `signoff` | "Best regards,\n<name>\n<email>" |

Any **extra key** you add to the YAML front matter of `candidate_profile.md`
(e.g. `education`) also becomes a `candidate.*` variable automatically.

## Editing tips

- Templates use [Jinja2](https://jinja.palletsprojects.com/) syntax: `{{ variable }}`,
  `{% for x in list %}`, `{% if x %}`, and `{# comment #}` (comments never render).
- Put a `{# How to edit ... #}` note at the top of each template.
- Only state claims that exist in `candidate_profile.md`. Unknown URLs, email
  addresses, numeric claims, and `## Do not claim` phrases are flagged by the
  grounding checker.
