"""Grounded document generation engine.

Deterministic and fully offline by default. An optional AI client may reorder /
rewrite the cover letter and email using ONLY supplied facts, but the result is
always passed through ``check_grounding`` and discarded if it introduces any
ungrounded claim.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from job_agent.content.candidate import CandidateProfile
from job_agent.content.grounding import check_grounding
from job_agent.content.requirements import JobRequirements, extract_requirements
from job_agent.content.templates import TemplateSet
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

_POSTING_FIELDS = (
    "external_id",
    "title",
    "company",
    "location",
    "description",
    "url",
    "salary",
    "easy_apply",
    "remote",
    "employment_type",
    "remote_policy",
    "timezone",
    "hours_per_week",
    "currency",
    "salary_period",
    "requirements",
    "source",
    "extra",
)
_POSTING_TEXT_FIELDS = (
    "title",
    "company",
    "location",
    "description",
    "url",
    "employment_type",
    "remote_policy",
    "timezone",
    "currency",
    "salary_period",
    "requirements",
    "source",
)

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "you",
    "are",
    "our",
    "that",
    "this",
    "have",
    "from",
    "will",
    "your",
    "role",
    "work",
    "team",
    "years",
    "year",
    "about",
    "into",
    "they",
    "their",
    "what",
    "when",
    "where",
    "plus",
    "more",
    "using",
    "help",
    "well",
    "strong",
    "good",
    "great",
    "need",
    "must",
    "also",
    "been",
}


def _attr(obj, name: str, default=""):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _posting_to_dict(posting) -> dict:
    job: dict = {}
    for name in _POSTING_FIELDS:
        job[name] = _attr(posting, name, "")
    platform = _attr(posting, "platform", "")
    job["platform"] = getattr(platform, "value", platform) if platform else ""
    for name in _POSTING_TEXT_FIELDS:
        value = job[name]
        job[name] = (
            value if isinstance(value, str) else ("" if value is None else str(value))
        )
    job["job_title"] = job["title"]
    job["company_name"] = job["company"]
    return job


def _tokenize(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9+#]+", (text or "").lower())
        if len(word) >= 3 and word not in _STOPWORDS
    }


def _lang_name(base: str, lang: str) -> str:
    """Prefer <name>_<lang>.md; the template set falls back to base names."""
    return f"{base}_{lang}" if lang != "en" else base


def _select_evidence(
    candidate: CandidateProfile, requirements: JobRequirements, lang: str = "en"
) -> list[str]:
    """Pick 3-5 verbatim bullets from facts + role bullets by token overlap."""
    pool: list[str] = []
    seen: set[str] = set()

    def add(items) -> None:
        for item in items or []:
            cleaned = str(item).strip()
            if not cleaned:
                continue
            tokens = _tokenize(cleaned)
            # Near-duplicate guard: the same achievement appears both as a
            # "fact" and as a role bullet with slightly different wording.
            if tokens and any(
                tokens and len(tokens & seen_tokens) / max(len(tokens), 1) >= 0.6
                for seen_tokens in _seen_token_sets
            ):
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            _seen_token_sets.append(tokens)
            pool.append(cleaned)

    _seen_token_sets: list[set[str]] = []

    primary, secondary = (
        (getattr(candidate, "facts_id", None), None)
        if lang == "id"
        else (getattr(candidate, "facts", None), None)
    )
    add(primary)
    if not pool:  # keep the generator usable on profiles that lack ID facts
        add(secondary or getattr(candidate, "facts", None))
    for role in (getattr(candidate, "roles_id", None) or []) if lang == "id" else (
        getattr(candidate, "roles", None) or []
    ):
        if isinstance(role, dict):
            add(role.get("bullets"))

    req_text = " ".join(
        list(requirements.must_have or [])
        + list(requirements.nice_to_have or [])
        + [
            str(requirements.seniority or ""),
            str(requirements.remote_policy or ""),
            str(requirements.timezone or ""),
        ]
    )
    req_tokens = _tokenize(req_text)

    def score(item: str) -> int:
        return len(_tokenize(item) & req_tokens)

    # Stable sort keeps original (source) order for equal scores.
    ranked = sorted(pool, key=score, reverse=True)
    return ranked[:5]

    _seen_token_sets.clear()


def _split_email(rendered: str, default_subject: str) -> tuple[str, str]:
    lines = (rendered or "").splitlines()
    subject = default_subject
    rest = lines
    if lines and lines[0].strip().lower().startswith("subject:"):
        subject = lines[0].strip()[len("Subject:") :].strip() or default_subject
        rest = lines[1:]
    while rest and not rest[0].strip():
        rest = rest[1:]
    return subject, "\n".join(rest).strip()


def _parse_ai_response(raw) -> tuple[str, str, str]:
    text = str(raw or "")

    def section(start: str, end: str | None) -> str:
        i = text.find(start)
        if i == -1:
            return ""
        i += len(start)
        j = text.find(end, i) if end else len(text)
        if j == -1:
            j = len(text)
        return text[i:j].strip()

    cover = section("=== COVER_LETTER ===", "=== EMAIL_SUBJECT ===")
    subject = section("=== EMAIL_SUBJECT ===", "=== EMAIL_BODY ===")
    body = section("=== EMAIL_BODY ===", None)
    if not cover or not body:
        raise ValueError("AI response missing delimited sections")
    return cover, subject, body


def _ai_rewrite(
    ai_client,
    *,
    candidate,
    job: dict,
    requirements: JobRequirements,
    evidence: list[str],
    matched_skills: list[str],
    greeting: str,
    signoff: str,
) -> tuple[str, str, str]:
    facts = ""
    try:
        facts = candidate.grounded_facts_text()
    except Exception:
        facts = "\n".join(f"- {f}" for f in (getattr(candidate, "facts", None) or []))
    evidence_text = "\n".join(f"- {e}" for e in evidence)
    system = (
        "You are a careful application writer. Use ONLY the verified facts "
        "supplied below. Never invent employers, dates, numbers, URLs, emails, "
        "or any claim that is not present in the facts."
    )
    prompt = (
        "Rewrite the cover letter and application email using ONLY these facts.\n\n"
        f"JOB: {job.get('title', '')} at {job.get('company', '')}\n"
        f"REQUIREMENTS: {', '.join(requirements.must_have)}\n"
        f"MATCHED SKILLS: {', '.join(matched_skills)}\n\n"
        "VERIFIED FACTS (the ONLY claims you may state):\n"
        f"{facts}\n\n"
        "SELECTED EVIDENCE (you may rephrase but must not add to):\n"
        f"{evidence_text}\n\n"
        f"GREETING: {greeting}\nSIGNOFF: {signoff}\n\n"
        "Return EXACTLY this structure and nothing else:\n"
        "=== COVER_LETTER ===\n<3 short paragraphs>\n"
        "=== EMAIL_SUBJECT ===\n<one line>\n"
        "=== EMAIL_BODY ===\n<short email body>\n"
    )
    raw = ai_client.complete(prompt, system=system)
    return _parse_ai_response(raw)


@dataclass
class GeneratedDocuments:
    resume_markdown: str
    cover_letter: str
    email_subject: str
    email_body: str
    connection_note: str = ""
    gig_profile: str = ""
    prompt_version: str = ""
    candidate_version: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def generate_documents(
    *,
    posting,
    profile: dict,
    candidate: CandidateProfile,
    template_set: TemplateSet,
    ai_client=None,
    matched_skills: list[str] | None = None,
    prompt_version: str = "2026-11-06",
    language: str = "en",
) -> GeneratedDocuments:
    """Render grounded documents. Deterministic/offline by default; never raises."""
    lang = (language or "en").strip().lower()
    warnings: list[str] = []

    def record(items) -> None:
        for item in items or []:
            if item not in warnings:
                warnings.append(item)

    try:
        job = _posting_to_dict(posting)
        profile_dict = dict(profile) if isinstance(profile, dict) else {}
        cc = candidate.as_context() if hasattr(candidate, "as_context") else {}

        requirements = extract_requirements(
            job.get("description", ""), title=job.get("title", "")
        )

        ms = list(matched_skills) if matched_skills else []
        if not ms:
            ms = list(requirements.must_have[:6]) or list(cc.get("skills_flat", [])[:6])
        # Cover-letter phrasing pairs must_have with matched skills; if they are
        # the same word the sentence repeats itself ("focus on React ... with
        # React"). Top up from the grounded skill list, minus must_haves.
        top_up = [
            s for s in cc.get("skills_flat", [])
            if s not in ms and s not in requirements.must_have
        ]
        for skill in top_up:
            if len(ms) >= 4:
                break
            ms.append(skill)

        evidence = _select_evidence(candidate, requirements, lang)

        name = str(cc.get("name") or "Candidate")
        email = str(cc.get("email") or "")
        if lang == "id":
            greeting = (
                f"Kepada Tim Rekrutmen {job['company']},"
                if job.get("company")
                else "Kepada Tim Rekrutmen,"
            )
            signoff = f"Hormat saya,\n{name}" + (f"\n{email}" if email else "")
        else:
            greeting = (
                f"Dear {job['company']} hiring team,"
                if job.get("company")
                else "Dear Hiring Manager,"
            )
            signoff = f"Best regards,\n{name}" + (f"\n{email}" if email else "")

        # Only named keys -- spreading the flat ``as_context()`` dict would put
        # a ``name`` key in **ctx and collide with ``render(self, name, ...)``.
        ctx = {
            "candidate": cc,
            "job": job,
            "requirements": requirements.as_context(),
            "evidence": evidence,
            "matched_skills": ms,
            # Phrase "hands-on experience with X" must not repeat must_haves
            "extra_skills": [s for s in ms if s not in requirements.must_have],
            "profile": profile_dict,
            "greeting": greeting,
            "signoff": signoff,
        }

        resume = template_set.render(_lang_name("resume_master", lang), **ctx)
        cover = template_set.render(_lang_name("cover_letter", lang), **ctx)
        email_rendered = template_set.render(_lang_name("email_apply", lang), **ctx)
        default_subject = (
            f"{job.get('title') or 'Role'} at {job.get('company') or 'the company'}"
            f" — {name}"
        )
        email_subject, email_body = _split_email(email_rendered, default_subject)
        note = template_set.render(_lang_name("connection_note", lang), **ctx)
        gig = template_set.render(_lang_name("gig_profile", lang), **ctx)

        # Grounding is ALWAYS run on the deterministic documents.
        for rendered in (resume, cover, email_body, note, gig):
            record(check_grounding(rendered, candidate))

        # Optional AI rewrite, always grounded; discarded on any warning.
        if ai_client is not None:
            try:
                ai_cover, ai_subject, ai_body = _ai_rewrite(
                    ai_client,
                    candidate=candidate,
                    job=job,
                    requirements=requirements,
                    evidence=evidence,
                    matched_skills=ms,
                    greeting=greeting,
                    signoff=signoff,
                )
                ai_warnings = check_grounding(ai_cover + "\n" + ai_body, candidate)
                if ai_warnings:
                    record(ai_warnings)  # discard AI, keep deterministic
                else:
                    cover = ai_cover
                    email_subject = ai_subject or email_subject
                    email_body = ai_body
            except Exception as exc:
                log.warning("ai_rewrite_failed", error=str(exc))

        return GeneratedDocuments(
            resume_markdown=resume,
            cover_letter=cover,
            email_subject=email_subject,
            email_body=email_body,
            connection_note=note,
            gig_profile=gig,
            prompt_version=prompt_version,
            candidate_version=getattr(candidate, "version", ""),
            warnings=warnings,
        )
    except Exception as exc:  # pragma: no cover - defensive, must never raise
        log.warning("generate_documents_failed", error=str(exc))
        return GeneratedDocuments(
            resume_markdown="",
            cover_letter="",
            email_subject="",
            email_body="",
            connection_note="",
            gig_profile="",
            prompt_version=prompt_version,
            candidate_version=getattr(candidate, "version", ""),
            warnings=warnings or [f"Generation fell back to empty documents: {exc}"],
        )


def save_documents(
    docs: GeneratedDocuments, out_dir: str | Path, key: str
) -> dict[str, str]:
    """Write all documents plus a manifest; return ``{name: path}``."""
    safe_key = re.sub(r"[^A-Za-z0-9_-]", "_", str(key)).strip("_") or "documents"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    subject = (docs.email_subject or "").strip()
    if subject.lower().startswith("subject:"):
        subject = subject[len("Subject:") :].strip()

    manifest = docs.as_dict()
    manifest["key"] = safe_key

    files: dict[str, tuple[str, str]] = {
        "resume": (f"{safe_key}_resume.md", docs.resume_markdown or ""),
        "cover_letter": (f"{safe_key}_cover_letter.md", docs.cover_letter or ""),
        "email": (
            f"{safe_key}_email.txt",
            f"Subject: {subject}\n\n{docs.email_body or ''}",
        ),
        "connection_note": (
            f"{safe_key}_connection_note.txt",
            docs.connection_note or "",
        ),
        "gig_profile": (f"{safe_key}_gig_profile.md", docs.gig_profile or ""),
        "manifest": (
            f"{safe_key}_manifest.json",
            json.dumps(manifest, indent=2, ensure_ascii=False),
        ),
    }

    paths: dict[str, str] = {}
    for name, (filename, content) in files.items():
        path = out / filename
        path.write_text(content, encoding="utf-8")
        paths[name] = str(path)
    return paths
