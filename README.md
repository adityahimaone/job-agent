# Job Agent

Autonomous job application agent that automates the entire job search lifecycle: discovering jobs on LinkedIn/Indeed/Glassdoor, scoring them against configurable profiles using AI, tailoring CVs for ATS optimization, auto-applying to high-confidence matches, queuing medium matches for review, and conducting LinkedIn recruiter outreach.

> **Current build (2026-11-06).** A full audit and implementation pass is in
> [`docs/AUDIT-2026-11-06.md`](docs/AUDIT-2026-11-06.md). Highlights: dry-run is
> safe by default, **every submission needs a human approval**, applications are
> verified before being recorded, documents are generated from editable Markdown
> templates grounded in verified facts, a spreadsheet is the approval surface,
> structured feeds + Ego Lite are the fast/human-supervised discovery lanes, and
> part-time/remote + AI-training gigs are first-class. Start with
> [`docs/README.md`](docs/README.md) for the docs index and quick start.

## Demo

https://github.com/billmal071/job-agent/releases/download/v0.1.0/demo.mp4

## Features

- **Multi-Platform Discovery** — LinkedIn, Indeed, Glassdoor, ZipRecruiter, Dice, Wellfound
- **External ATS Support** — Automatically handles Greenhouse, Lever, Workday, Ashby, and generic application forms when job boards redirect to company sites
- **AI-Powered Matching** — Scores jobs 0.0–1.0 against your profile using configurable AI providers
- **Multi-AI Provider** — Google Gemini (free), Groq (free), OpenRouter, Ollama (local), Anthropic Claude
- **Resume Tailoring** — Auto-generates ATS-optimized resumes per job (PDF)
- **Cover Letters** — AI-generated, customizable tone (professional, casual, formal)
- **Screening Question Answering** — AI fills out application screening questions (multiple choice, free text, dropdowns)
- **CV-to-Profile Generator** — Upload your resume and AI creates your search profile automatically
- **Approval-Gated Autonomy** — high scores (≥0.90) are queued for approval; medium (0.70–0.89) queued; low (<0.70) skipped. Nothing is submitted unattended.
- **Grounded Documents** — resume, cover letter, email subject/body and connection note rendered from editable Markdown templates + verified candidate facts, with a no-fabrication check
- **Spreadsheet Approval Surface** — local CSV (works out of the box) or Google Sheets; edit a `decision` column to approve/reject, then `job-agent sheet pull`
- **Fast Feed Lane** — Remotive, RemoteOK, Arbeitnow, Greenhouse/Lever/Ashby boards and LinkedIn guest search fetched concurrently
- **Part-time / Remote Filters** — employment type, hours/week, timezone alignment, hourly rate and keyword filters applied to every posting
- **SEA + AI-Training Coverage** — JobStreet, Glints, Kalibrr, Seek board specs via Ego Lite; Alignerr-style gig lane with hourly rates and assessment tracking
- **Ego Lite Apply** — resume upload, human handoff for login/2FA/CAPTCHA/final submit, and confirmation-based verification
- **Web Dashboard** — Flask + HTMX + Bootstrap 5 with overview, jobs, review queue, applications, outreach, analytics, settings
- **Cold Email Outreach** — AI-generated personalized cold emails and LinkedIn connection requests to recruiters
- **Easy Apply Detection** — Identifies and prioritizes one-click apply jobs on LinkedIn, Indeed, and Glassdoor
- **Anti-Detection** — Camoufox stealth browser, human-like typing/mouse, random delays, session rotation
- **Email-to-Apply** — Detects "email your resume" pages and sends applications via SMTP with resume/cover letter attached
- **Scheduling** — APScheduler with configurable activity windows (default 8am–11pm)
- **Notifications** — Email (SMTP), webhooks (Slack/Discord), Telegram
- **Interactive Bots** — Telegram bot (long-polling) and Discord bot (slash commands) for approving/rejecting jobs, checking queue and stats from your phone
- **Job Bookmarking** — Bookmark interesting jobs for later review
- **Cross-Platform Dedup** — Detects duplicate postings across platforms by normalized title + company
- **Follow-Up Reminders** — Dashboard shows applications pending 7+ days without response
- **Stats Digest** — Generate and email daily/weekly summary of job search activity
- **Bulk Actions** — Approve or reject multiple queued jobs at once from the dashboard
- **Export Documents** — Download tailored resumes and cover letters per application
- **Proxy Support** — Route browser traffic through HTTP/SOCKS proxies
- **Security** — Fernet-encrypted credentials, SQLite outside repo, browser state persistence

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
# Clone and install
git clone https://github.com/billmal071/job-agent.git
cd job-agent
uv sync

# Install Playwright browsers
uv run playwright install chromium

# Initialize the database
uv run job-agent init-db

# Start the dashboard
uv run job-agent dashboard
# Visit http://127.0.0.1:5000/settings to configure everything from the UI
```

> **Tip:** You can configure your AI provider, platform credentials, matching thresholds, and notifications entirely from the dashboard — no need to edit config files.

Alternatively, configure via `.env`:
```bash
cp .env.example .env
# Edit .env — set your AI provider and API key
# Free options: Gemini (default), Groq, Ollama (local)
```

### Create Your Profile

The build ships with a real profile for this workspace
(`config/profiles/aditya.yaml`) and an editable knowledge base
(`config/templates/candidate_profile.md`). Edit those two files — they are
plain Markdown/YAML and are meant to be reviewed by hand.

```bash
# 1. Your verified facts (roles, projects, links, constraints)
open config/templates/candidate_profile.md

# 2. Your search (keywords, part-time/remote filters, exclusions)
open config/profiles/aditya.yaml

# 3. Validate profile + every template
uv run job-agent content check

# 4. Preview a grounded cover letter + email for a sample job
uv run job-agent content preview --company "Acme" --title "Part-time Design Engineer"
```

You can still generate a profile from a CV in the dashboard
(`/settings` → "Generate Profile from CV"), but review the output: the grounded
templates only use facts you confirm.

### Run the Pipeline

```bash
# Fast, no-browser discovery from public feeds
uv run job-agent feeds -q "design engineer" -n 20

# Dry-run (default): discover + match, write nothing
uv run job-agent run --profile config/profiles/aditya.yaml --once

# Create the approval sheet, push jobs, edit decision=approve, pull back
uv run job-agent sheet template
uv run job-agent sheet push
uv run job-agent sheet pull

# Submit only approved jobs (approval is still required per job)
uv run job-agent apply-approved --live

# AI-training gigs (account + assessment lane)
uv run job-agent gigs refresh
uv run job-agent gigs list

# Run on a schedule (hourly, single instance, activity window respected)
uv run job-agent run --profile config/profiles/aditya.yaml

# Search one platform without applying
uv run job-agent search --platform linkedin --query "Design Engineer" --location "Remote"
```

### First-Time Login

On the first run for each platform, you'll need to log in manually in the browser window:

1. Set `headless: false` in `config/default.yaml` under `browser:`
2. Run the pipeline — a browser window will open
3. Complete the login (including any 2FA/CAPTCHA challenges)
4. The agent waits up to 120 seconds for you to finish
5. Your session is saved to `~/.job-agent/browser_state/` and reused on future runs

**Indeed & Glassdoor** use Google OAuth — when the "Sign in with Google" button appears, click it and complete the Google login flow in the popup. The agent detects this automatically.

Once sessions are saved, you can switch back to `headless: true` for unattended runs.

## How the Pipeline Works

```
Feeds ─┐
Boards ─┼→ Deduplicate → Match → Decide → Approve → Tailor → Apply → Track → Sheet
Ego ───┘
```

1. **Discover** — Fast structured feeds first, then board searches (Playwright or Ego Lite) using your profile keywords and locations
2. **Deduplicate** — Skips jobs already seen (external ID matching)
3. **Match** — AI scores each job 0.0–1.0 against your profile (skills, experience, salary, location)
4. **Decide** — Based on score:
   - **≥ 0.90** → Queue for approval (submitted only after you approve)
   - **0.70 – 0.89** → Queue for manual review in dashboard
   - **< 0.70** → Skip
5. **Approve** — High scorers wait in the dashboard / sheet until you approve them
6. **Tailor** — Renders a resume, cover letter, email subject/body and connection note from your editable Markdown templates, grounded in verified facts
7. **Apply** — Submits only approved jobs, then verifies the confirmation before recording success:
   - **Easy Apply** jobs → fills the platform's quick-apply form
   - **External ATS** (Greenhouse, Lever, Workday, Ashby) → navigates to the ATS, detects form fields, uploads resume, fills screening questions via AI, and submits
   - **Email apply** pages → sends resume + cover letter via SMTP
   - **Generic forms** → AI-powered field detection and filling
8. **Track** — Stores results in the database with status, match reasoning, verification state and error details, then mirrors them into the sheet

## Dashboard

The web dashboard at `http://127.0.0.1:5000` provides:

| Page | Description |
|------|-------------|
| **Overview** | Summary stats, recent activity, agent run history, follow-up reminders for stale applications |
| **Jobs** | All discovered jobs with match scores, skill insights, bookmark toggle, duplicate badges, filtering by status |
| **Review Queue** | Medium-scoring jobs awaiting approval. Bulk approve/reject with checkboxes, select-all |
| **Applications** | Submitted applications with status tracking, retry failed apps, download tailored resumes and cover letters |
| **Outreach** | AI-generated cold emails and LinkedIn connection request drafts. Track engagement status |
| **Analytics** | Score distribution histogram, activity timeline, platform breakdown charts, stats digest |
| **Settings** | AI provider, platform credentials, matching thresholds, email/Slack notifications, CV-to-profile generator |

### Dashboard Actions

From the dashboard you can:

- **Run the pipeline** on demand (discover, match, apply)
- **Approve/reject** jobs in the review queue (individually or in bulk)
- **Bookmark** jobs for later review
- **Retry** failed applications
- **Download** tailored resumes and cover letters per application
- **Configure AI provider** and API keys (no restart needed)
- **Add platform credentials** (encrypted with Fernet)
- **Adjust thresholds** for auto-apply and review queue
- **Upload your CV** to auto-generate a search profile
- **Set up notifications** — email alerts and Slack/Discord webhooks
- **Export** application data to CSV

## Telegram & Discord Bots

Manage your job queue from your phone via interactive bot commands.

### Telegram Bot

```bash
# Interactive setup (writes to .env for you)
uv run job-agent bot setup

# Or set manually in .env:
# JOB_AGENT_TELEGRAM_BOT_TOKEN=your-token-from-botfather
# JOB_AGENT_TELEGRAM_CHAT_ID=your-chat-id

# Start the bot (long-polling, no public URL needed)
uv run job-agent bot telegram
```

### Discord Bot

Discord uses slash commands via HTTP interactions — requires the dashboard to be publicly accessible.

```bash
# Set in .env:
# JOB_AGENT_DISCORD_BOT_TOKEN=your-bot-token
# JOB_AGENT_DISCORD_APPLICATION_ID=your-app-id
# JOB_AGENT_DISCORD_PUBLIC_KEY=your-public-key

# Register slash commands (instant for a specific guild)
uv run job-agent bot discord-register --guild-id YOUR_GUILD_ID

# Start the dashboard (serves the /discord/interactions endpoint)
uv run job-agent dashboard
```

Set your Discord Interactions Endpoint URL to `https://your-domain/discord/interactions`.

### Bot Commands

| Command | Description |
|---------|-------------|
| `/queue` | List jobs awaiting review |
| `/approve <id>` | Approve a queued job |
| `/reject <id>` | Reject a queued job |
| `/stats` | Show job and application statistics |
| `/bookmarks` | List bookmarked jobs |
| `/help` | Show available commands |

## Multiple Profiles

Create multiple profiles for different job searches:

```bash
config/profiles/fullstack.yaml    → config/resumes/master.pdf
config/profiles/devops.yaml       → config/resumes/master-devops.pdf

# Run each independently
uv run job-agent run --profile config/profiles/fullstack.yaml --once
uv run job-agent run --profile config/profiles/devops.yaml --once
```

Each profile specifies its own keywords, locations, skills, salary expectations, and resume template.

## Docker

```bash
cp .env.example .env
# Edit .env with your keys

# Start the dashboard
docker compose up dashboard

# Run the agent once
docker compose run --rm agent
```

## AI Providers

Job Agent supports multiple AI providers. Choose the one that works best for you:

| Provider | Cost | Setup |
|----------|------|-------|
| **Google Gemini** (default) | Free (15 RPM) | Get key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| **Groq** | Free tier | Get key at [console.groq.com](https://console.groq.com) |
| **Ollama** | Free (local) | Install from [ollama.com](https://ollama.com), then `ollama pull llama3.1` |
| **OpenRouter** | Some free models | Get key at [openrouter.ai/keys](https://openrouter.ai/keys) |
| **Anthropic Claude** | Paid | Get key at [console.anthropic.com](https://console.anthropic.com) |

Set via the **dashboard Settings page** at `http://127.0.0.1:5000/settings`, or in `.env`:
```bash
JOB_AGENT_AI_PROVIDER=gemini          # or groq, ollama, openrouter, anthropic
JOB_AGENT_GEMINI_API_KEY=your-key     # set the key for your chosen provider
```

## Configuration

### Profile Schema

```yaml
name: "Senior Python Developer"

search:
  keywords: ["Senior Python Developer", "Backend Engineer"]
  locations: ["Remote", "San Francisco, CA"]
  experience_level: "senior"        # entry, mid, senior, lead
  remote_preference: "remote_first" # onsite, hybrid, remote_only, remote_first
  salary_minimum: 150000

skills:
  required: ["Python", "SQL", "REST APIs"]
  preferred: ["FastAPI", "Django", "AWS", "Docker"]

exclusions:
  companies: ["SpamCorp"]
  keywords: ["unpaid", "intern"]

resume_template: "master"
cover_letter_tone: "professional"   # professional, casual, formal
```

Or just upload your CV from the dashboard and let AI generate this for you.

### Rate Limits

Configurable per platform in `config/default.yaml`:

| Platform      | Requests/min | Apps/day | Session  | Cooldown |
|---------------|-------------|----------|----------|----------|
| LinkedIn      | 3           | 25       | 45 min   | 30 min   |
| Indeed        | 5           | 40       | 60 min   | 20 min   |
| Glassdoor     | 4           | 30       | 60 min   | 20 min   |
| ZipRecruiter  | 5           | 40       | 60 min   | 20 min   |
| Dice          | 4           | 35       | 60 min   | 20 min   |
| Wellfound     | 3           | 20       | 45 min   | 25 min   |

### Autonomy Thresholds

| Score       | Action                    |
|-------------|---------------------------|
| ≥ 0.90      | Queue for approval        |
| 0.70 – 0.89 | Queue for manual review   |
| < 0.70      | Skip                      |

Thresholds are configurable from `config/default.yaml` or the dashboard Settings page. With `agent.require_approval_for_all: true` (the default) even a 0.99 job waits for your approval. Per-platform `max_applications_per_day` is enforced before every submission.

## External ATS Support

When a job board redirects to a company's own application page, Job Agent detects the ATS and handles it automatically:

| ATS | Detection | Capabilities |
|-----|-----------|-------------|
| **Greenhouse** | `boards.greenhouse.io` | Resume upload, field filling, submit |
| **Lever** | `jobs.lever.co` | Resume upload, field filling, hCaptcha detection |
| **Workday** | `myworkdayjobs.com` | Resume upload, multi-step form navigation |
| **Ashby** | `jobs.ashbyhq.com` | Resume upload, field filling, submit |
| **Generic** | Any form with file upload | AI-powered field detection, resume upload, screening questions |
| **Email Apply** | `mailto:` links or email-only pages | Sends resume + cover letter via SMTP |

Screening questions (multiple choice, dropdowns, free text) are answered automatically using AI based on your profile.

## Project Structure

```
job-agent/
├── config/
│   ├── default.yaml         # System defaults (rate limits, scheduling, browser)
│   ├── profiles/            # Job search profiles (YAML)
│   └── resumes/             # Master resume templates (PDF/MD)
├── src/job_agent/
│   ├── ai/                  # AI client, matching, resume tailoring, cover letters,
│   │                        #   screening question answerer, cold email generator,
│   │                        #   CV-to-profile generator
│   ├── bots/                # Interactive bots: Telegram (long-polling), Discord (slash commands)
│   ├── browser/             # Playwright/Camoufox: manager, stealth, humanizer, auth
│   ├── dashboard/           # Flask web UI: routes, templates, static assets
│   ├── db/                  # SQLAlchemy models, session management, repositories
│   ├── notifications/       # Email (SMTP), webhook (Slack/Discord), Telegram notifiers
│   ├── orchestrator/        # Pipeline engine, scheduler, review queue, stats digest
│   ├── platforms/           # Platform drivers:
│   │   ├── linkedin/        #   Discovery + Easy Apply + external redirect
│   │   ├── indeed/          #   Discovery + Easy Apply + external redirect
│   │   ├── glassdoor/       #   Discovery + Easy Apply + external redirect
│   │   ├── ziprecruiter/    #   Discovery + application
│   │   ├── dice/            #   Discovery + application
│   │   ├── wellfound/       #   Discovery + application
│   │   └── external_ats.py  #   Greenhouse, Lever, Workday, Ashby, generic, email
│   └── utils/               # Crypto, logging, rate limiter
└── tests/                   # Unit and integration tests
```

## Security

- Dashboard requires a token (`~/.job-agent/dashboard_token`, printed on first start) and enforces CSRF on every mutating request
- The Flask debugger is **off** by default (`dashboard.debug: false`)
- Platform credentials are Fernet-encrypted, key stored at `~/.job-agent/fernet.key` (0600 perms)
- API keys live in `.env` (gitignored) or are set via the dashboard
- Database at `~/.job-agent/agent.db` (SQLite, outside repo), schema managed by versioned Alembic migrations
- Browser state at `~/.job-agent/browser_state/` (cookies, localStorage)
- Dry-run is the default; `--live` is required to submit, and submissions still need approval
- All sensitive data is stored outside the repo directory

## Testing

```bash
uv run pytest tests/ -q   # 416 tests
```

If your environment's shared pytest temp directory throws `EEXIST` (seen under
sandboxed shells), use a fresh base temp directory:

```bash
TMPDIR=$(mktemp -d) uv run pytest -q --basetemp="$TMPDIR/base"
```

## License

MIT
