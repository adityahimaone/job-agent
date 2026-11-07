"""Configuration loading with Pydantic settings (env → YAML → defaults)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseModel):
    activity_start_hour: int = 8
    activity_end_hour: int = 23
    schedule_interval: int = 60
    # Code default stays False for unit tests; config/default.yaml sets True for
    # real deployments so applications never happen unattended by accident.
    dry_run: bool = False
    require_approval_for_all: bool = True  # No unattended submissions.
    max_run_minutes: int = 25  # Hard wall-clock budget for one pipeline run.
    cache_ai_responses: bool = True
    ai_request_timeout: int = 60  # seconds for AI API requests
    ollama_request_timeout: int = 120  # seconds for local Ollama requests


class MatchingConfig(BaseModel):
    auto_apply_threshold: float = 0.80
    review_threshold: float = 0.70
    model: str = ""
    # Kept for backwards compatibility; use the two explicit budgets below.
    max_ai_calls_per_run: int = 20
    max_match_calls_per_run: int = 40
    max_generation_calls_per_run: int = 60
    max_cost_usd_per_run: float = 1.0


class ContentConfig(BaseModel):
    """Human-editable Markdown templates and grounded document generation."""

    mode: str = "grounded"  # "grounded" | "legacy"
    templates_dir: str = "config/templates"
    candidate_profile: str = "config/templates/candidate_profile.md"
    output_dir: str = ""  # empty = <data_dir>/documents
    require_grounding: bool = True
    prompt_version: str = "2026-11-06"
    # "en" default; "id" uses <name>_id.md template variants when present.
    language: str = "en"


class SheetsConfig(BaseModel):
    """Spreadsheet mirror + human approval surface."""

    enabled: bool = True
    backend: str = "csv"  # "csv" | "google"
    directory: str = "~/.job-agent/sheets"
    spreadsheet_id: str = ""
    # Service-account JSON, or an OAuth client_secret (then token_path is used).
    credentials_path: str = ""
    token_path: str = "~/.hermes/google_token.json"
    pull_decisions: bool = True


class FeedsConfig(BaseModel):
    """Fast discovery through public JSON/RSS feeds."""

    enabled: bool = True
    config_path: str = "config/discovery_sources.yaml"
    max_workers: int = 6
    timeout: int = 12


class GigsConfig(BaseModel):
    """AI-training / expert-network opportunities."""

    enabled: bool = True
    networks: list[str] = Field(default_factory=lambda: ["alignerr"])
    min_hourly_rate: float = 20.0
    remote_only: bool = True


class ResumeConfig(BaseModel):
    master_resume: str = "config/resumes/master.pdf"
    cover_letter_tone: str = "professional"
    use_latex: bool = False  # Opt-in: use LaTeX for PDF generation (requires texlive)
    default_cover_note: str = (
        "I'm excited about this opportunity and believe my skills "
        "are a strong match. Please see my attached resume for details."
    )


class PlatformConfig(BaseModel):
    enabled: bool = False
    max_requests_per_minute: int = 5
    max_applications_per_day: int = 30
    session_duration_minutes: int = 45
    cooldown_minutes: int = 20
    max_connections_per_day: int = 0
    use_camoufox: bool | None = None
    use_cdp: bool | None = None
    use_ego: bool = False


class PlatformsConfig(BaseModel):
    linkedin: PlatformConfig = PlatformConfig(
        enabled=True,
        max_requests_per_minute=3,
        max_applications_per_day=25,
        max_connections_per_day=20,
        session_duration_minutes=45,
        cooldown_minutes=30,
    )
    indeed: PlatformConfig = PlatformConfig(
        max_requests_per_minute=5,
        max_applications_per_day=40,
        session_duration_minutes=60,
        use_camoufox=False,
        use_cdp=True,
    )
    glassdoor: PlatformConfig = PlatformConfig(
        max_requests_per_minute=4,
        max_applications_per_day=30,
        session_duration_minutes=60,
        use_camoufox=False,
        use_cdp=True,
    )
    ziprecruiter: PlatformConfig = PlatformConfig(
        max_requests_per_minute=5,
        max_applications_per_day=40,
        session_duration_minutes=60,
        cooldown_minutes=20,
    )
    dice: PlatformConfig = PlatformConfig(
        max_requests_per_minute=4,
        max_applications_per_day=35,
        session_duration_minutes=60,
        cooldown_minutes=20,
    )
    wellfound: PlatformConfig = PlatformConfig(
        max_requests_per_minute=3,
        max_applications_per_day=20,
        session_duration_minutes=45,
        cooldown_minutes=25,
    )
    dealls: PlatformConfig = PlatformConfig(
        enabled=False,
        use_ego=True,
        max_requests_per_minute=3,
        max_applications_per_day=0,
    )


class BrowserConfig(BaseModel):
    headless: bool = True
    state_dir: str = "~/.job-agent/browser_state"
    proxy: str | None = None
    use_camoufox: bool = True
    cdp_url: str = "http://localhost:9222"
    viewport_width: int = 1920
    viewport_height: int = 1080


class EgoConfig(BaseModel):
    """ego(lite) browser bridge. `host` empty runs ego-browser locally."""

    enabled: bool = False
    host: str = ""
    user: str = ""
    binary: str = ""
    timeout: int = 180
    task_space: str = "job-agent"
    settle_ms: int = 2500
    apply_enabled: bool = True
    require_human_submit: bool = True
    board_config_path: str = "config/discovery_sources.yaml"


class EmailNotificationConfig(BaseModel):
    enabled: bool = False
    triggers: list[str] = Field(default_factory=lambda: ["failed"])


class WebhookNotificationConfig(BaseModel):
    enabled: bool = False
    triggers: list[str] = Field(
        default_factory=lambda: ["auto_applied", "queued", "failed"]
    )


class TelegramNotificationConfig(BaseModel):
    enabled: bool = False
    triggers: list[str] = Field(
        default_factory=lambda: ["queued", "failed", "auto_applied"]
    )


class DiscordNotificationConfig(BaseModel):
    enabled: bool = False
    triggers: list[str] = Field(
        default_factory=lambda: ["queued", "failed", "auto_applied"]
    )


class NotificationsConfig(BaseModel):
    email: EmailNotificationConfig = EmailNotificationConfig()
    webhook: WebhookNotificationConfig = WebhookNotificationConfig()
    telegram: TelegramNotificationConfig = TelegramNotificationConfig()
    discord: DiscordNotificationConfig = DiscordNotificationConfig()


class DashboardConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 5000
    debug: bool = False
    # Code defaults are permissive so unit tests can use create_app() directly.
    # config/default.yaml enables both for real deployments.
    auth_enabled: bool = False
    auth_token: str = ""  # empty = read/generate <data_dir>/dashboard_token
    csrf_enabled: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOB_AGENT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # AI provider: anthropic, gemini, groq, openrouter, ollama
    ai_provider: str = "gemini"

    # API keys from environment (set the one for your chosen provider)
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    # OpenAI-compatible gateway override (9router, LiteLLM, etc.)
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    flask_secret_key: str = "change-me"
    database_url: str = ""

    # SMTP settings
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    notification_email: str = ""

    # Webhooks
    slack_webhook_url: str = ""
    discord_webhook_url: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Discord bot (for interactive slash commands)
    discord_bot_token: str = ""
    discord_application_id: str = ""
    discord_public_key: str = ""

    # Ollama
    ollama_url: str = "http://localhost:11434"

    # Proxy
    proxy_url: str = ""

    # Nested config from YAML
    agent: AgentConfig = AgentConfig()
    matching: MatchingConfig = MatchingConfig()
    resume: ResumeConfig = ResumeConfig()
    platforms: PlatformsConfig = PlatformsConfig()
    browser: BrowserConfig = BrowserConfig()
    ego: EgoConfig = EgoConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    dashboard: DashboardConfig = DashboardConfig()
    content: ContentConfig = ContentConfig()
    sheets: SheetsConfig = SheetsConfig()
    feeds: FeedsConfig = FeedsConfig()
    gigs: GigsConfig = GigsConfig()

    def model_post_init(self, __context) -> None:
        """Wire top-level proxy_url into browser.proxy if not already set."""
        if self.proxy_url and not self.browser.proxy:
            self.browser.proxy = self.proxy_url

    @property
    def data_dir(self) -> Path:
        path = Path.home() / ".job-agent"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.data_dir / 'agent.db'}"

    @property
    def documents_dir(self) -> Path:
        path = (
            Path(self.content.output_dir).expanduser()
            if self.content.output_dir
            else self.data_dir / "documents"
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def sheets_dir(self) -> Path:
        path = Path(self.sheets.directory).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def resolve_path(self, value: str) -> Path:
        """Resolve a config path relative to the project root, else expand ~."""
        p = Path(value).expanduser()
        if p.is_absolute():
            return p
        return (Path.cwd() / p).resolve()

    def dashboard_token(self) -> str:
        """Return a stable auth token, generating and persisting one if needed."""
        if self.dashboard.auth_token:
            return self.dashboard.auth_token
        path = self.data_dir / "dashboard_token"
        if path.exists():
            return path.read_text().strip()
        import secrets

        token = secrets.token_urlsafe(24)
        path.write_text(token + "\n")
        path.chmod(0o600)
        return token


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _strip_env_overridden_keys(
    yaml_data: dict[str, Any], prefix: str = "JOB_AGENT_"
) -> dict[str, Any]:
    """Remove YAML keys that have a corresponding environment variable set.

    This ensures env vars always take precedence over YAML values, since
    Pydantic treats explicit kwargs as higher priority than env sources.
    """
    import os

    result = {}
    for key, value in yaml_data.items():
        env_key = f"{prefix}{key.upper()}"
        if isinstance(value, dict):
            # Recurse into nested dicts with nested delimiter
            nested = _strip_env_overridden_keys(value, f"{env_key}__")
            if nested:
                result[key] = nested
        elif env_key not in os.environ:
            result[key] = value
    return result


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load settings from YAML config file, with env vars taking precedence."""
    yaml_data: dict[str, Any] = {}

    # Load default YAML
    default_path = Path("config/default.yaml")
    if default_path.exists():
        with open(default_path) as f:
            yaml_data = yaml.safe_load(f) or {}

    # Load override YAML if specified
    if config_path:
        override_path = Path(config_path)
        if override_path.exists():
            with open(override_path) as f:
                override = yaml.safe_load(f) or {}
            yaml_data = _deep_merge(yaml_data, override)

    # Strip keys that have env var overrides so Pydantic uses the env value
    yaml_data = _strip_env_overridden_keys(yaml_data)

    return Settings(**yaml_data)


def load_profile(profile_path: str | Path) -> dict[str, Any]:
    """Load a job search profile from YAML."""
    with open(profile_path) as f:
        return yaml.safe_load(f) or {}
