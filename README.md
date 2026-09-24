# Naukri.com Job Auto-Applier

An autonomous, AI-assisted bot that searches Naukri.com for matching jobs
across multiple roles and cities, scores each one for relevance with an LLM,
auto-applies within a short human-override window, fills out questionnaire
forms in natural language, and emails you at every step. Runs 24/7 on an
AWS EC2 instance inside Docker.

## Features

- **Multi-role, multi-city search** — loops through every combination of
  target roles and locations defined in `config.json` (e.g. 5 roles × 9
  cities = 45 searches per cycle).
- **AI relevance scoring (Groq LLM)** — every job description is scored
  0-100 against your resume profile before anything else happens; low-scoring
  jobs are silently skipped so you never see noise.
- **Auto-apply with a safety window** — once a job passes the relevance
  filter, you get an email with the job details and a **Skip** button. If you
  don't click it within a short grace period, the bot applies automatically —
  keeping throughput high without fully removing human oversight.
- **Global pause switch** — a **Pause Auto-Apply** control (email button or
  webhook) instantly halts all future applications while the bot keeps
  scanning and emailing, independent of any single job's decision.
- **AI-powered questionnaire answering** — open-ended application questions
  (e.g. "Tell us about yourself", "How do you handle failure?") are answered
  automatically by an LLM in natural, human, first-person language grounded
  strictly in your real resume — never fabricated anecdotes.
- **Automatic structured field filling** — recognizes common fields (CTC,
  notice period, skills) and fills them from `config.json`.
- **Confirmation emails** — after each successful application, you get an
  email with the job title, company, location, link, and every field/answer
  submitted on your behalf.
- **Persistent login** — uses a saved Playwright `storage_state.json`
  session (no password re-entry), portable across machines/containers.
- **Bot-detection resistant** — runs Chromium in headed mode via Xvfb with
  stealth patches, since Naukri's Akamai WAF blocks headless browsers.
- **Web-based control panel** — Flask endpoints (`/start`, `/stop`,
  `/auto-apply/pause`, `/auto-apply/resume`) triggered from email links,
  usable from a phone browser, and locked behind a per-deploy secret token
  so nobody but you can control the bot.
- **Live public dashboard** — a React/Vite single-page app (`dashboard/`)
  served read-only at `/dashboard`, showing bot status, today's stats, and
  application history, polling the JSON API every few seconds. Safe to make
  public since it exposes no credentials or control actions.
- **Remote Naukri login capture (phone-friendly)** — click "Login to Naukri"
  on the dashboard to open a live, noVNC-streamed real browser window, fully
  proxied over HTTPS so it also works from a mobile browser; log in manually
  (handles OTP/CAPTCHA naturally), click "Save Session", and the bot picks up
  the new session automatically. This is the primary, everyday way to
  refresh an expired session — no laptop or local Python required.
  `login_setup.py` (below) is only a local fallback.
- **HTTPS on a free custom domain** — a Caddy reverse-proxy container
  auto-provisions and renews a Let's Encrypt certificate for a DuckDNS
  domain, so the dashboard is reachable at a stable `https://` URL from any
  device.
- **Daily summary emails** — a nightly digest of jobs found vs. applied.
- **Always-on cloud hosting** — deployed on an AWS EC2 instance with an
  Elastic IP so the webhook URLs never change, and `restart: unless-stopped`
  so it recovers automatically from reboots/crashes.

## Architecture

| Component | Purpose |
|---|---|
| `app.py` | Flask server: app control (`/start`, `/stop`), auto-apply pause/resume, decision/clarification webhooks, JSON API, dashboard hosting, and token-gated security |
| `bot.py` | Playwright automation: search, AI scoring, auto-apply, form-filling, emails |
| `ai_helper.py` | Groq LLM integration: relevance scoring, JD summarization, questionnaire answering |
| `mailer.py` | SMTP email sending |
| `login_setup.py` | **Fallback local** script to log in to Naukri and save `naukri_storage_state.json` (run outside Docker, only needed if the remote flow below is unavailable) |
| `login_capture.py` | **Primary remote/phone-friendly** noVNC-driven login capture, spawned by `app.py`'s `/login/start` route; validates the homepage loaded before saving the session |
| `dashboard/` | React + Vite frontend: live status/stats/applications panels, session/login panel |
| `Caddyfile` | Reverse proxy config: auto HTTPS for the public DuckDNS domain |
| `config.json` | Target roles, locations, experience filter, questionnaire answers, blacklist, AI settings |
| `profile.json` | Structured resume profile used to ground AI scoring/answers |
| `Dockerfile` | Python + Xvfb + Playwright Chromium image, with the pre-built dashboard bundled in |
| `docker-compose.yml` | `naukri-bot` + `caddy` services, env vars, volumes, `APP_BASE_URL` |

## How it works

1. `login_setup.py` is run once locally (headed browser) to authenticate and
   save a portable session file (`naukri_storage_state.json`).
2. The bot container starts, loads that session, and confirms it's still
   valid by visiting the authenticated dashboard.
3. For each `(location, role)` pair in `config.json`, it searches Naukri,
   iterates job cards, and skips anything already processed
   (`seen_jobs.json`) or blacklisted.
4. For each new job, it fetches the full job description and asks the Groq
   LLM to score its relevance (0-100) against your resume profile
   (`profile.json`). Jobs below the configured threshold are skipped silently.
5. For jobs that pass, an email is sent immediately with the job details and
   a **Skip** button. If no response arrives within the grace period, the
   bot proceeds to apply automatically (unless auto-apply has been paused
   globally via `/auto-apply/pause`).
6. On applying, it clicks Naukri's native Apply button, fills recognized
   structured fields (CTC, notice period, skills) from `config.json`, and
   routes any open-ended questionnaire question through the LLM
   (`ai_helper.answer_question`) to produce a natural, resume-grounded
   answer — falling back to a clarification email only if AI is unavailable.
7. A confirmation email is sent with every field/answer submitted, and
   session cookies are refreshed and saved back to
   `naukri_storage_state.json` at the end of each run.

## Deployment

Hosted on AWS EC2 (Ubuntu, `eu-north-1`) with:
- Docker + Docker Compose running the bot (`naukri-bot`) and a `caddy`
  reverse-proxy container.
- An **Elastic IP** attached to the instance so `APP_BASE_URL` (used in all
  email links) stays constant across instance stops/restarts.
- A free **DuckDNS** domain pointed at the Elastic IP, fronted by **Caddy**,
  which auto-provisions/renews a Let's Encrypt HTTPS certificate — giving a
  stable `https://<your-subdomain>.duckdns.org` URL that redirects to
  `/dashboard`.
- Security group allowing inbound SSH (22), HTTP/HTTPS (80/443), and the
  Flask app port (5000, for direct IP access/debugging).
- `restart: unless-stopped` policy on both containers for automatic recovery.

**Instance control:** start/stop the EC2 instance from the AWS Console
(desktop or mobile app) → EC2 → instance → Start/Stop. As long as the
Elastic IP stays associated, the public IP and webhook links never change.

**Monitoring:** primarily via email notifications (job found, applied,
clarification needed) and the live dashboard. Logs can also be tailed via
SSH:
```
ssh -i <key>.pem ubuntu@<elastic-ip> "docker logs job_bot-naukri-bot-1 --tail 50"
```

### Building/deploying the dashboard

The dashboard is a static build, not served by a dev server in production:
```bash
cd dashboard
npm install
npm run build          # outputs dashboard/dist, bundled into the Docker image
```
Then rebuild and restart the container as usual
(`docker compose up -d --build naukri-bot`).

## Configuration (`config.json`)

```json
{
  "target_roles": ["Software Engineer", "..."],
  "filters": { "experience_years": ["0", "1", "2", "3"], "locations": ["bangalore", "..."] },
  "questionnaire_answers": {
    "current_ctc": "...", "expected_ctc": "...",
    "notice_period": "...", "skills": "..."
  },
  "blacklist_companies": ["..."],
  "email_target": "you@example.com"
}
```

## Local setup

```bash
pip install -r requirements.txt
python login_setup.py        # one-time: log in, save session (local, headed browser)
cd dashboard && npm install && npm run build && cd ..  # build the dashboard once
docker compose up -d --build # build and run the bot (+ caddy, if PUBLIC_DOMAIN is set)
```

Once deployed, you can also capture/refresh the Naukri session remotely via
the dashboard's "Login to Naukri" button (`login_capture.py` + noVNC),
without needing local Python/Playwright at all.

## Security notes

This repo is private because it references personal job-search data. The
following are git-ignored and must be supplied per-environment, never
committed: `.env`, `config.json`, `naukri_storage_state.json`, `*.pem` SSH
keys, `seen_jobs.json`, `decisions/`, and `naukri_profile/`.

All administrative/control routes (`/start`, `/stop`, `/auto-apply/pause`,
`/auto-apply/resume`, `/decide/<id>`, `/clarify/<id>`, `/edit`,
`/solve-challenge`, `/login/*`) require a `?token=` query parameter (or
hidden form field) matching `CHALLENGE_ACCESS_TOKEN` from `.env` — requests
without it get a `403 Forbidden`. This token is embedded automatically in
every email action link, so normal usage is unaffected; it only blocks
unauthorized requests from the public dashboard domain. Read-only endpoints
(`/dashboard`, `/api/*`) remain public with no token required.
