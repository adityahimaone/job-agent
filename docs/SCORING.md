# Scoring: how jobs are matched, and the thin-description floor

`JobMatcher.match` (`src/job_agent/ai/job_matcher.py`) scores a posting 0.0–1.0
against the active profile. `decide_job` then applies the thresholds from
`config/default.yaml`: `>= 0.90` auto-approve (QUEUED when
`require_approval=True`, as the Muse lane always does), `>= 0.50` review
queue, below that skip. The review threshold was loosened 0.55 -> 0.50
on 2026-11-11 at Aditya's request ("jangan terlalu strict"): scores
0.50-0.54 are borderline fits, and the queue is human review, not
auto-apply, so sending them there is the cheaper error.

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

- The 0.90 auto-apply threshold is untouched; the review threshold
  is 0.50 (loosened from 0.55 on 2026-11-11, see top of this file).
- Exclusions, the title prefilter, and rich-description skill prefilter
  behave exactly as before.
## Live re-score

`job-agent rescore [--dry-run] [--limit N]` (`src/job_agent/discovery/rescore.py`)
re-scores stored SKIPPED/DISCOVERED jobs with the current matcher and
`decide_job(..., require_approval=True)`, so the best outcome is QUEUED.
APPLIED jobs are never touched and no application is ever created. An
existing match result is updated in place (no duplicate rows). First
run 2026-11-11 after the 0.55 -> 0.50 loosening, at Aditya's request.
