"""Flask application factory for the web dashboard."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from job_agent.config import Settings
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

# Endpoints that must remain reachable without a dashboard session.
PUBLIC_PATHS = ("/login", "/health", "/static/", "/favicon.ico")


def _secret_key(settings: Settings) -> str:
    """Use a persistent random key instead of the insecure shipped default."""
    configured = settings.flask_secret_key
    if configured and configured != "change-me":
        return configured
    path = Path(settings.data_dir) / "flask_secret.key"
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_hex(32)
    path.write_text(key + "\n")
    path.chmod(0o600)
    return key


def _install_security(app: Flask, settings: Settings) -> None:
    """Session auth + CSRF protection for every mutating dashboard request."""
    auth_enabled = settings.dashboard.auth_enabled
    csrf_enabled = settings.dashboard.csrf_enabled
    app.config["AUTH_ENABLED"] = auth_enabled
    app.config["CSRF_ENABLED"] = csrf_enabled
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    def is_public(path: str) -> bool:
        return any(
            path == p or path.startswith(p) for p in PUBLIC_PATHS
        ) or path.startswith("/discord/interactions")

    def csrf_value() -> str:
        token = session.get("csrf")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf"] = token
        return token

    @app.context_processor
    def inject_security():
        return {
            "csrf_token": csrf_value,
            "auth_enabled": auth_enabled,
            "csrf_enabled": csrf_enabled,
        }

    @app.before_request
    def guard():  # pragma: no cover - exercised through the test client
        path = request.path
        if auth_enabled and not is_public(path) and not session.get("ja_auth"):
            if request.headers.get("HX-Request"):
                response = app.make_response("")
                response.headers["HX-Redirect"] = "/login"
                return response
            return redirect(url_for("login", next=request.full_path))
        if (
            csrf_enabled
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and not is_public(path)
        ):
            supplied = request.headers.get("X-CSRF-Token") or request.form.get(
                "csrf_token", ""
            )
            if not supplied or not secrets.compare_digest(
                supplied, session.get("csrf", "")
            ):
                abort(403, description="Invalid or missing CSRF token")
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = ""
        if request.method == "POST":
            supplied = request.form.get("token", "")
            if secrets.compare_digest(supplied, settings.dashboard_token()):
                session["ja_auth"] = True
                session.permanent = False
                return redirect(request.args.get("next") or "/")
            error = "That token did not match."
        return render_template("login.html", error=error, auth_enabled=auth_enabled)

    @app.route("/logout")
    def logout():
        session.pop("ja_auth", None)
        return redirect("/login")


def create_app(settings: Settings | None = None) -> Flask:
    """Create and configure the Flask application."""
    if settings is None:
        from job_agent.config import load_settings

        settings = load_settings()

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["SECRET_KEY"] = _secret_key(settings)
    app.config["SETTINGS"] = settings

    # Custom Jinja2 filters
    def from_json(value):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []

    app.jinja_env.filters["from_json"] = from_json
    app.jinja_env.globals["now"] = lambda: datetime.now(timezone.utc)

    # Make settings available in templates
    @app.context_processor
    def inject_settings():
        return {"settings": settings}

    _install_security(app, settings)

    # Register routes
    from job_agent.dashboard.routes.overview import bp as overview_bp
    from job_agent.dashboard.routes.jobs import bp as jobs_bp
    from job_agent.dashboard.routes.queue import bp as queue_bp
    from job_agent.dashboard.routes.applications import bp as applications_bp
    from job_agent.dashboard.routes.outreach import bp as outreach_bp
    from job_agent.dashboard.routes.analytics import bp as analytics_bp
    from job_agent.dashboard.routes.content_page import bp as content_bp
    from job_agent.dashboard.routes.settings_page import bp as settings_bp

    app.register_blueprint(overview_bp)
    app.register_blueprint(jobs_bp, url_prefix="/jobs")
    app.register_blueprint(queue_bp, url_prefix="/queue")
    app.register_blueprint(applications_bp, url_prefix="/applications")
    app.register_blueprint(outreach_bp, url_prefix="/outreach")
    app.register_blueprint(analytics_bp, url_prefix="/analytics")
    app.register_blueprint(content_bp, url_prefix="/content")
    app.register_blueprint(settings_bp, url_prefix="/settings")

    from job_agent.dashboard.routes.actions import bp as actions_bp
    from job_agent.dashboard.routes.discord_webhook import bp as discord_bp
    from job_agent.dashboard.routes.runs import bp as runs_bp

    app.register_blueprint(actions_bp, url_prefix="/actions")
    app.register_blueprint(discord_bp, url_prefix="/discord")
    app.register_blueprint(runs_bp, url_prefix="/runs")

    # Health check
    @app.route("/health")
    def health():
        try:
            app_version = version("job-agent")
        except PackageNotFoundError:
            app_version = "unknown"
        return jsonify({"status": "ok", "version": app_version})

    # Teardown session
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        pass  # Sessions are managed per-request in routes

    return app
