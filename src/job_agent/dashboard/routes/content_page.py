"""Content page: browse the 5 document templates, preview generated docs, email.

Read-only by default: rendering is deterministic (no AI), so a preview never
costs a model call. The one write action is sending the generated email through
the Hermes Google OAuth token.
"""

from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from job_agent.content.candidate import load_candidate_profile
from job_agent.content.generator import generate_documents
from job_agent.content.templates import load_template_set
from job_agent.db.repository import JobRepository
from job_agent.db.session import get_session
from job_agent.platforms.base import JobPosting, Platform

bp = Blueprint("content_page", __name__)

DOC_NAMES = (
    "resume_master",
    "cover_letter",
    "email_apply",
    "connection_note",
    "gig_profile",
)

# Rendered per application language; every other doc is English-only.
BILINGUAL = ("cover_letter", "email_apply", "connection_note")
LANGS = ("en", "id")
# The resume is never generated: applications attach the published PDF.
RESUME_PDF_URL = "https://adityahimaone.space/resume.pdf"


def _settings():
    return current_app.config["SETTINGS"]


def _templates():
    s = _settings()
    return load_template_set(s.resolve_path(s.content.templates_dir))


def _posting_for(job_id: int) -> JobPosting | None:
    s = _settings()
    session = get_session(s)
    try:
        job = JobRepository(session).get_by_id(job_id)
        if not job:
            return None
        return JobPosting(
            external_id=job.external_id,
            platform=job.platform or Platform.OTHER,
            title=job.title,
            company=job.company,
            description=job.description or "",
            url=job.url or "",
            location=job.location or "",
            employment_type=job.employment_type or "",
            remote=bool(job.remote),
        )
    finally:
        session.close()


@bp.route("/")
def index():
    """List the 5 templates + the grounded candidate profile."""
    s = _settings()
    ts = _templates()
    files = []
    for name in DOC_NAMES + ("candidate_profile",):
        langs = []
        for lang in LANGS if name in BILINGUAL else ("en",):
            path = ts.directory / f"{name if lang == 'en' else f'{name}_{lang}'}.md"
            langs.append(
                {
                    "lang": lang,
                    "exists": path.is_file(),
                    "size": path.stat().st_size if path.is_file() else 0,
                }
            )
        files.append(
            {
                "name": name,
                "langs": langs,
                "bilingual": name in BILINGUAL,
                "is_resume": name == "resume_master",
                "exists": langs[0]["exists"],
                "size": sum(item["size"] for item in langs),
            }
        )
    candidate = load_candidate_profile(s.resolve_path(s.content.candidate_profile))
    return render_template(
        "content/index.html",
        files=files,
        candidate=candidate,
        templates_dir=str(ts.directory),
        documents_dir=str(s.documents_dir),
        resume_pdf_url=RESUME_PDF_URL,
    )


@bp.route("/template/<name>")
def template_view(name: str):
    """Raw source of one template or the candidate profile."""
    base_name = name[:-3] if name.endswith("_id") else name
    if base_name not in DOC_NAMES + ("candidate_profile",) or (
        base_name not in BILINGUAL and name.endswith("_id")
    ):
        flash("Unknown template.", "danger")
        return redirect(url_for("content_page.index"))
    s = _settings()
    ts = _templates()
    base = (
        s.resolve_path(s.content.candidate_profile).parent
        if name == "candidate_profile"
        else ts.directory
    )
    path = base / f"{name}.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else "(missing)"
    return render_template(
        "content/template.html", name=name, text=text, path=str(path)
    )


@bp.route("/preview")
def preview():
    """Render all documents for one job (deterministic, no AI call)."""
    s = _settings()
    job_id = request.args.get("job_id", type=int)
    docs = None
    posting = None
    warnings: list[str] = []
    if job_id:
        posting = _posting_for(job_id)
        if posting is None:
            flash(f"Job {job_id} not found.", "danger")
        else:
            candidate = load_candidate_profile(
                s.resolve_path(s.content.candidate_profile)
            )
            docs = generate_documents(
                posting=posting,
                profile={},
                candidate=candidate,
                template_set=_templates(),
                ai_client=None,
                prompt_version=s.content.prompt_version,
                language=(request.args.get("lang") or s.content.language),
            )
            warnings = list(docs.warnings)
    return render_template(
        "content/preview.html",
        job_id=job_id,
        posting=posting,
        docs=docs,
        warnings=warnings,
        lang=(request.args.get("lang") or s.content.language),
        resume_pdf_url=RESUME_PDF_URL,
    )


@bp.route("/email", methods=["POST"])
def send_email():
    """Send the generated application email through the Hermes Google token."""
    job_id = request.form.get("job_id", type=int)
    to_addr = (request.form.get("to") or "").strip()
    if not job_id or not to_addr:
        flash("Job id and recipient email are required.", "danger")
        return redirect(url_for("content_page.preview", job_id=job_id or ""))

    posting = _posting_for(job_id)
    if posting is None:
        flash(f"Job {job_id} not found.", "danger")
        return redirect(url_for("content_page.index"))

    s = _settings()
    candidate = load_candidate_profile(s.resolve_path(s.content.candidate_profile))
    docs = generate_documents(
        posting=posting,
        profile={},
        candidate=candidate,
        template_set=_templates(),
        ai_client=None,
        prompt_version=s.content.prompt_version,
        language=(request.form.get("lang") or s.content.language),
    )

    from job_agent.content.resume_asset import canonical_resume
    from job_agent.integrations.gmail_send import send_email as gmail_send

    filename, resume_bytes = canonical_resume()
    try:
        result = gmail_send(
            to_addr,
            docs.email_subject,
            docs.email_body,
            attachments=[
                (filename, resume_bytes, "application/pdf"),
            ],
        )
    except Exception as exc:  # surfaced, never swallowed
        flash(f"Send failed: {type(exc).__name__}: {exc}", "danger")
        return redirect(url_for("content_page.preview", job_id=job_id))

    flash(
        f"Email sent to {to_addr} via {result.get('from') or 'Google account'} "
        f"(message id {result.get('id', '?')}).",
        "success",
    )
    return redirect(url_for("content_page.preview", job_id=job_id))