# Naukri.com Job Auto-Applier

An autonomous bot that searches Naukri.com for matching jobs across multiple
roles and cities, pauses for your approval before applying, fills out
questionnaire forms automatically, and emails you at every step. Runs 24/7 on
an AWS EC2 instance inside Docker.

## Features

- **Multi-role, multi-city search** — loops through every combination of
  target roles and locations defined in `config.json` (e.g. 5 roles × 9
  cities = 45 searches per cycle).
- **Human-in-the-loop approval** — for every new job found, an email is sent
  with **✅ Apply** / **❌ Skip** buttons. The bot pauses until you respond
  (or times out after 1 hour and skips).
- **Automatic questionnaire filling** — recognizes common fields (CTC,
  notice period, skills) and fills them from `config.json`. Unrecognized
  fields trigger a **clarification email** so you can supply the answer
  directly, and the bot waits for it before continuing.
- **Confirmation emails** — after each successful application, you get an
  email with the job title, company, location, link, and your full profile
  details used (experience, CTC, notice period, skills).
- **Persistent login** — uses a saved Playwright `storage_state.json"
  session (no password re-entry), portable across machines/containers.
- **Bot-detection resistant** — runs Chromium in headed mode via Xvfb with
  stealth patches, since Naukri's Akamai WAF blocks headless browsers.
- **Web-based start/stop control** — Flask endpoints (`/start`, `/stop`)
  triggered from email links, usable from a phone browser.
- **Always-on cloud hosting** — deployed on an AWS EC2 instance with an
  Elastic IP so the webhook URLs never change, and `restart: unless-stopped`
  so it recovers automatically from reboots/crashes.

## Architecture

| Component | Purpose |
|---|---|
| `app.py` | Flask server: app control (`/start`, `/stop`) and decision/clarification webhooks |
| `bot.py` | Playwright automation: search, apply, form-filling, emails |
| `mailer.py` | SMTP email sending |
| `login_setup.py` | One-time local script to log in to Naukri and save `naukri_storage_state.json` |
| `config.json` | Target roles, locations, experience filter, questionnaire answers, blacklist |
| `Dockerfile` | Python + Xvfb + Playwright Chromium image |
| `docker-compose.yml` | Container definition, env vars, volumes, `APP_BASE_URL` |

## How it works

1. `login_setup.py` is run once locally (headed browser) to authenticate and
   save a portable session file (`naukri_storage_state.json`).
2. The bot container starts, loads that session, and confirms it's still
   valid by visiting the authenticated dashboard.
3. For each `(location, role)` pair in `config.json`, it searches Naukri,
   iterates job cards, and skips anything already processed
   (`seen_jobs.json`) or blacklisted.
4. For each new job, it emails you a decision request and waits for your
   click (`/decide/<job_id>?action=apply|skip`).
5. On **Apply**, it clicks Naukri's native Apply button, fills any
   questionnaire fields it recognizes, asks for help via email on anything
   it doesn't recognize, then sends a confirmation email with full details.
6. Session cookies are refreshed and saved back to `naukri_storage_state.json`
   at the end of each run.

## Deployment

Hosted on AWS EC2 (Ubuntu, `eu-north-1`) with:
- Docker + Docker Compose running the bot as a single service.
- An **Elastic IP** attached to the instance so `APP_BASE_URL` (used in all
  email links) stays constant across instance stops/restarts.
- Security group allowing inbound SSH (22) and the Flask app port (5000).
- `restart: unless-stopped` policy for automatic recovery.

**Instance control:** start/stop the EC2 instance from the AWS Console
(desktop or mobile app) → EC2 → instance → Start/Stop. As long as the
Elastic IP stays associated, the public IP and webhook links never change.

**Monitoring:** primarily via email notifications (job found, applied,
clarification needed). Logs can also be tailed via SSH:
```
ssh -i <key>.pem ubuntu@<elastic-ip> "docker logs job_bot-naukri-bot-1 --tail 50"
```

## Configuration (`config.json`)

```json
{
  "target_roles": ["Software Engineer", "..."],
  "filters": { "experience_years": "0", "locations": ["bangalore", "..."] },
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
python login_setup.py        # one-time: log in, save session
docker compose up -d --build # build and run the bot
```

## Security notes

This repo is private because it references personal job-search data. The
following are git-ignored and must be supplied per-environment, never
committed: `.env`, `config.json`, `naukri_storage_state.json`, `*.pem` SSH
keys, `seen_jobs.json`, `decisions/`, and `naukri_profile/`.
