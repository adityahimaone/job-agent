# Templates & grounded documents — user guide

This guide is for editing the **Markdown files by hand**. You do not need to be
a developer, you do not need an AI key, and nothing here needs the internet.
Everything the agent writes about you comes from one file you control.

---

## 1. What "grounded" means

Older versions of the app asked an AI to "write a cover letter from a list of
skills". That can invent jobs, numbers, and links that are not true. The
grounded engine works differently:

- **One source of truth** — `config/templates/candidate_profile.md` holds every
  fact about you.
- **Templates** — the other `.md` files are fill-in-the-blank letters and resumes.
- **No invention** — the engine only picks from facts you wrote. A "grounding
  checker" then scans the finished text and warns about any URL, email, number,
  or forbidden phrase that is not in your knowledge base.
- **Offline & deterministic** — with no AI configured you get the same documents
  every time, instantly.

---

## 2. The three moving parts

| Part | File(s) | You edit it to… |
| --- | --- | --- |
| Knowledge base | `config/templates/candidate_profile.md` | Add facts, roles, projects, skills, links, constraints |
| Templates | `config/templates/*.md` | Change the wording of letters / resume / email |
| Search profile | `config/profiles/*.yaml` | Change what jobs the agent looks for |

---

## 3. Quick start (offline, no AI key)

The engine is used automatically when `content.mode: grounded` (the default).
The easiest preview is the CLI (no AI key, no network):

```bash
uv run job-agent content check
uv run job-agent content preview \
  --company "Example Co" \
  --title "Part-time Frontend Engineer" \
  --description "Remote part-time role. React, TypeScript. GMT+7."
```

That writes the rendered resume, cover letter, email (with subject line),
connection note, gig profile and a manifest to `~/.job-agent/documents/`, and
prints any grounding warnings.

You can also call the engine directly:

```bash
uv run python -c "
from job_agent.content import load_candidate_profile, load_template_set, generate_documents
from job_agent.platforms.base import JobPosting
from job_agent.db.models import Platform

p = load_candidate_profile('config/templates/candidate_profile.md')
t = load_template_set('config/templates')
j = JobPosting(external_id='demo', platform=Platform.LINKEDIN,
               title='Frontend Engineer', company='Example Co', location='Remote',
               description='Part-time remote role. React, TypeScript. GMT+7.')
d = generate_documents(posting=j, profile={}, candidate=p, template_set=t,
                       matched_skills=['React', 'TypeScript'])
print(d.cover_letter)
print('Warnings:', d.warnings)
"
```

`Warnings: []` means every claim in the letter is grounded. No network call was
made. Change a fact in `candidate_profile.md`, re-run, and the documents change.

---

## 4. The knowledge base: `candidate_profile.md`

This file has two parts: a **YAML front matter** block (between `---` lines) and
Markdown **sections** below it.

### 4.1 Front matter (scalars and structured data)

```yaml
---
name: Aditya Himawan
email: adityahimaone@gmail.com
location: Jakarta, Indonesia
timezone: GMT+7
headline: "Design Engineer / Frontend Engineer"
years_experience: 4
links:
  portfolio: https://adityahimaone.space
  github: https://github.com/adityahimaone
  linkedin: https://linkedin.com/in/adityahimaone
skills:
  Frontend: [React, Next.js, TypeScript]
constraints:
  remote_only: true
  employment_types: [part_time, contract, freelance, full_time]
  hours_per_week: 20
  min_hourly_rate: 30
  currency: USD
  timezone_alignment: "GMT+7 ±4"
  availability: "part-time, remote"
education:
  - "BSc Informatics, Universitas AMIKOM Yogyakarta"
---
```

Rules:
- Keep it **valid YAML**: quote strings that contain `:`, `±`, or `/` when in
  doubt; keep indentation with spaces (no tabs).
- `years_experience` is the canonical number. Templates render it as `4+ years`.
- Every URL you want to appear in a document must be declared in `links` (or in
  a verified fact). Otherwise the checker flags it.
- **Any extra key** you add (like `education`) automatically becomes a
  `candidate.<key>` variable in templates. Add as many as you like.

### 4.2 `## Verified facts`

A bullet list of one-line, verifiable claims. These are the only claims the
engine is allowed to make.

```
## Verified facts

- 4+ years of professional experience building production web applications.
- Built Bisadaya, a job-seeker SaaS serving 15K+ users, with Next.js SSR.
```

**How to add a fact:** add a new `- ` (or `* `) line under this heading. Numbers
you want to reuse ("4+ years", "15K+ users") must appear here verbatim, because
the checker looks for them here. Bullets can use `-` or `*`; missing sections
and missing front matter are tolerated.

### 4.3 `## Experience`

One role per `###` header, with `|` separated fields, then bullets:

```
## Experience

### Frontend SaaS Developer | PT Fatiha Sakti | Oct 2022–Present | Jakarta
- Built Bisadaya, a job-seeker SaaS serving 15K+ users, with Next.js SSR.
- Shipped Campus Connect in 3 months.
```

Header fields: `Title | Company | Dates | Location`. The bullets beneath become
`role.bullets` in templates.

### 4.4 `## Projects`

```
## Projects

### Switchyard | Go, SQLite, React
A local-first tool for managing feature flags.
```

Header fields: `Name | Stack | URL`. The URL is optional (leave it out or use
`-`). A descriptive line underneath becomes the project blurb.

### 4.5 `## Do not claim`

A short human note plus a bullet list of **exact phrases** that must never appear
in a generated document. The checker flags any of them.

```
## Do not claim

Note: present "4+ years", never state 6 years.

- 6 years
- 10+ years
- staff engineer
- led a team of
```

Keep these phrases short — they are matched literally against the finished text.

---

## 5. The templates

All templates are plain Markdown with Jinja2 fill-ins. The first line of each is
a `{# How to edit ... #}` comment — it is a note to you and never appears in the
output.

| Template | Produces | Notes |
| --- | --- | --- |
| `candidate_profile.md` | (not rendered — it *is* the data) | Edit facts here |
| `resume_master.md` | `resume_markdown` | Generic headings: `## Summary`, `## Experience`, `## Projects`, `## Skills`, `## Education`. Used as the PDF/Markdown source. |
| `cover_letter.md` | `cover_letter` | Three short paragraphs. |
| `email_apply.md` | `email_subject` + `email_body` | **First line must stay `Subject: ...`**, followed by a blank line, then the body. The engine splits subject from body automatically. |
| `connection_note.md` | `connection_note` | Keep the finished note under 300 characters (LinkedIn's limit). |
| `gig_profile.md` | `gig_profile` | Short bio for AI-training / expert networks; emphasises coding, design systems, and AI workflow automation. |

Each template renders **standalone** — with no AI — using the variables below.

### 5.1 All placeholder variables

| Variable | Type | Example use |
| --- | --- | --- |
| `candidate.name` | text | `{{ candidate.name }}` |
| `candidate.email` | text | `{{ candidate.email }}` |
| `candidate.location`, `candidate.timezone`, `candidate.headline` | text | `{{ candidate.headline }}` |
| `candidate.years_experience` | number | `{{ candidate.years_experience }}+ years` |
| `candidate.years_experience_label` | text | `"4+ years"` ready-made |
| `candidate.links.portfolio` / `.github` / `.linkedin` | URL | `{{ candidate.links.portfolio }}` |
| `candidate.facts`, `candidate.facts_text` | list / text | `{{ candidate.facts_text }}` |
| `candidate.skills` | dict (category → list) | `{% for cat, items in candidate.skills.items() %}` |
| `candidate.skills_flat` | list | `{{ candidate.skills_flat|join(', ') }}` |
| `candidate.roles` | list of dicts | `role.title`, `role.company`, `role.dates`, `role.location`, `role.bullets` |
| `candidate.projects` | list of dicts | `p.name`, `p.stack`, `p.url`, `p.blurb` |
| `candidate.constraints` | dict | `availability`, `timezone_alignment`, `employment_types`, `remote_only`, `hours_per_week`, `min_hourly_rate`, `currency` |
| `candidate.do_not_claim` | list | (used by the checker, not usually rendered) |
| *any extra front-matter key* | any | `{{ candidate.education }}` |
| `job.title`, `job.company`, `job.location`, `job.description`, `job.url`, `job.employment_type`, `job.remote_policy`, `job.timezone` | text | `{{ job.company }}` |
| `requirements.must_have`, `requirements.nice_to_have` | lists | `{{ requirements.must_have|join(', ') }}` |
| `requirements.employment_types`, `requirements.remote_policy`, `requirements.timezone`, `requirements.seniority`, `requirements.summary` | text / list | `{{ requirements.remote_policy }}` |
| `evidence` | list of 3–5 grounded bullets | `{% for e in evidence %}- {{ e }}{% endfor %}` or `{{ evidence|join(', ') }}` |
| `matched_skills` | list | `{{ matched_skills|join(', ') }}` |
| `profile` | dict | the active search profile |
| `greeting` | text | `{{ greeting }}` → "Dear Acme hiring team," |
| `signoff` | text | `{{ signoff }}` → "Best regards, …" |

Syntax cheatsheet: `{{ variable }}` prints a value, `{% for x in list %} … {% endfor %}`
repeats a block, `{% if x %} … {% endif %}` includes a block conditionally, and
`{{ list|join(', ') }}` joins a list. If you reference a variable that does not
exist, the engine does **not** crash — it falls back to a safe rendering with
that blank left empty.

---

## 6. How fabrication is prevented

`check_grounding(text, profile)` scans finished documents and returns warnings.
It is a **heuristic, not a guarantee** — always give important applications a
human read. It flags:

1. **URLs** not present in `candidate.links` or inside a verified fact.
   → Add the link to `links` before using it.
2. **Email addresses** that are not `candidate.email`.
3. **Numeric claims** such as `4+ years`, `15K+ users`, `100 users`, `40%` that
   are not found verbatim in `## Verified facts`.
   → Put the exact number in a verified fact first.
4. **Forbidden phrases** — any bullet from `## Do not claim` that appears in the text.

### The AI guard (optional)

If an AI client is configured, it may **reorder / reword** the cover letter and
email using only the supplied facts. Its output is **always** passed through
`check_grounding`. If even one warning appears, the AI version is thrown away
and the deterministic version is kept, with the warnings recorded in
`GeneratedDocuments.warnings`. With no AI client, the deterministic version is
used directly. This means an AI can improve flow but can never smuggle in an
unverified claim.

---

## 7. `content.mode`: grounded vs legacy

In `config/default.yaml`:

```yaml
content:
  mode: "grounded"   # grounded | legacy
```

- **`grounded`** (default, recommended) — uses this engine: Markdown knowledge
  base + templates, offline, with grounding checks.
- **`legacy`** — uses the older AI generators (`ai/cover_letter.py`,
  `ai/cold_email.py`, `ai/resume_tailor.py`), which need an AI key and can
  hallucinate.

You can switch without editing the file by setting an environment variable:

```bash
JOB_AGENT_CONTENT__MODE=legacy job-agent ...
```

Related settings: `content.templates_dir`, `content.candidate_profile`,
`content.output_dir`, `content.require_grounding`, `content.prompt_version`.
`prompt_version` and the knowledge-base hash (`candidate_version`) are stamped
onto every generated document so you can tell exactly which facts/templates
produced it.

---

## 8. Preview & validate

- **Preview one template** — render it directly:
  ```python
  from job_agent.content import load_template_set, load_candidate_profile
  ts = load_template_set('config/templates')
  p = load_candidate_profile('config/templates/candidate_profile.md')
  ctx = {'candidate': p.as_context(), 'job': {'title': 'Frontend Engineer', 'company': 'Example Co'},
         'requirements': {'must_have': ['React']}, 'evidence': p.facts[:3],
         'matched_skills': ['React'], 'greeting': 'Dear Example Co hiring team,',
         'signoff': 'Best regards,\n' + p.name}
  print(ts.render('cover_letter', **ctx))
  ```
- **Check grounding** of any text:
  ```python
  from job_agent.content import check_grounding, load_candidate_profile
  p = load_candidate_profile('config/templates/candidate_profile.md')
  print(check_grounding('We served 999K users at http://fake.example', p))
  ```
- **Full generation + save** — `generate_documents(...)` returns a
  `GeneratedDocuments`; `save_documents(docs, out_dir, key)` writes the files.
- Everything works with `ai_client=None`; no network or API keys required.

---

## 9. Where generated documents are written

`save_documents(docs, out_dir, key)` writes (with `key` sanitised to letters,
digits, `-` and `_`):

```
<out_dir>/<key>_resume.md
<out_dir>/<key>_cover_letter.md
<out_dir>/<key>_email.txt          (Subject: line, blank line, then body)
<out_dir>/<key>_connection_note.txt
<out_dir>/<key>_gig_profile.md
<out_dir>/<key>_manifest.json      (versions, warnings, all fields)
```

`out_dir` comes from `content.output_dir`; when it is empty (the default) the
app uses `~/.job-agent/documents` (`Settings.documents_dir`). The manifest
records `prompt_version`, `candidate_version` (a short hash of the knowledge
base) and any grounding warnings, so every file is traceable.

---

## 10. Troubleshooting

| Symptom | Likely cause & fix |
| --- | --- |
| Documents say "Candidate" / are nearly empty | `candidate_profile.md` was not found. The engine returns a safe empty profile instead of crashing. Check `content.candidate_profile` path. |
| A warning about an unknown URL | Add the URL to `links` in the front matter, or remove it from the template. |
| A warning about "X years / Xk+ users" | Put that exact phrase into `## Verified facts`, or rephrase the template to avoid the number. |
| A warning about a do-not-claim phrase | Remove that phrase from the template/document, or (if it is now accurate) remove it from `## Do not claim`. |
| A template shows a blank where text should be | You referenced a variable that does not exist; the engine left it empty rather than crashing. Check the variable table above. |
| Role / project sections are empty | Sections must use `### Header | field | field` lines. Check the `## Experience` / `## Projects` formatting. |
| Front matter seems ignored | It must be a valid YAML block at the very top, fenced by `---` lines. Fix indentation/quoting. |
| AI rewrite keeps getting discarded | It introduced an ungrounded URL/number/email. The warnings in `GeneratedDocuments.warnings` explain why; the deterministic version is kept. |
| I want the old AI behaviour | Set `content.mode: "legacy"` (see section 7). |

---

*Remember: the grounding checker is a safety net, not a substitute for reading
your application before sending it.*
