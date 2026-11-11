# Scoring: how jobs are matched, and the thin-description floor

`JobMatcher.match` (`src/job_agent/ai/job_matcher.py`) scores a posting 0.0–1.0
against the active profile. `decide_job` then applies the thresholds from
`config/default.yaml`: `>= 0.90` auto-approve (QUEUED when
`require_approval=True`, as the Muse lane always does), `>= 0.55` review
queue, below that skip.

## Order of operations

1. **Hard exclusions first** — excluded location / company / keyword
   (e.g. India, `intern`, `junior`) score 0.00, no AI call. These are never
   lifted by anything below.
2. **Title prefilter** — the title must share a token with a search keyword,
   otherwise 0.00 (`title_mismatch`).
3. **Skill prefilter** — title + description together must mention at least
   one required or preferred skill, otherwise 0.00 (`no_skill_overlap`).
   Exception: when the description is **thin** (under 40 words) **and** the
   title is a frontend role (`frontend` / `front-end` / `UI engineer` /
   `web developer`), the job passes through to scoring instead of
   hard-zeroing. One-line Muse/Ego imports ("Frontend Developer.
   Singapore.") carry no skill words at all, and the old description-only
   check zeroed real frontend roles this way (FINN, StarHub, Capgemini,
   Coinbase, GudangAda all scored 0.00 in Nov 2026).
4. **JEV** (typesafe.ai, when `TYPESAFE_API_KEY` is set), else the OpenRouter
   chat matcher. Matched/missing skills are counted from title + description.
5. **Thin-description floor** — applied after a successful score, never
   after a scoring failure (`match_error`, `parse_error`):
   - description under 40 words + frontend-role title + 2 or more stack
     skills in title/description → floor **0.60**
   - same, with fewer than 2 stack skills → floor **0.55** (exactly the
     review threshold, so the job queues for a human)
   - rich descriptions and non-frontend titles: no floor; a score above
     the floor is never lowered.

   Rationale: JEV/chat scores for one-line postings cluster around
   0.29–0.52 even for genuine fits — the model penalises the missing
   evidence, not a real misfit. The queue is human review, not auto-apply,
   so sending thin-but-plausibly-fitting frontend roles there is the
   cheaper error. The floor is a `max()`: it only lifts, never lowers.

## Skill matching detail

Short skills (3 chars or fewer, e.g. `Go`) match on word boundaries only,
so "going" / "good" / "Google" do not count as Go. Longer skills keep the
substring check ("React" matches "React.js").

## What this does NOT change

- The 0.55 review threshold and 0.90 auto-apply threshold are untouched.
- Exclusions, the title prefilter, and rich-description skill prefilter
  behave exactly as before.
- A live re-score of already-stored jobs is a separate, explicit step:
  this change only affects jobs matched after it lands. Re-running it over
  the DB would newly queue ~40-46 thin frontend-role jobs currently below
  0.55 (measured 2026-11-11) — Aditya should approve that re-run first.
