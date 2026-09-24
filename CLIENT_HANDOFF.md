# Naukri.com Job Auto-Applier — Client Handoff

## What this is

An always-on bot that searches Naukri.com for jobs matching your target
roles/cities, scores each one with AI for relevance to your resume,
auto-applies within a short human-override window, fills out application
forms in natural language, and emails you at every step — with a live
web dashboard for monitoring and control from any device.

**Live dashboard:** `https://naukri-bot.duckdns.org/dashboard`
**Hosting:** AWS EC2 (Ubuntu), Docker Compose, HTTPS via Caddy + DuckDNS.

## What it does, end to end

1. Scans every combination of your configured roles × cities × experience
   range (currently 5 roles × 9 cities).
2. Scores each new job 0–100 for relevance using an LLM, grounded in your
   actual resume — irrelevant jobs are silently skipped.
3. For jobs that pass, you get an email with the job details and a **Skip**
   button. If you don't respond within the grace period, it applies
   automatically — high throughput, but you can always intervene.
4. Fills structured fields (CTC, notice period, skills) from your config,
   and answers open-ended questionnaire questions via AI in your voice.
5. Sends a confirmation email after every successful application, and a
   daily summary at night.

## How to operate it (day to day)

All of this works from your phone — no laptop or installed software needed.

| Action | How |
|---|---|
| Check status / stats / application history | Open the dashboard URL |
| Start / Stop the bot | Buttons in the status email, or dashboard |
| Pause/resume auto-apply (keep scanning, stop applying) | Email buttons |
| Refresh an expired login session | Tap "Login to Naukri" link in the alert email → log in on the live embedded browser (works on mobile) → tap "Save Session" → bot resumes automatically |
| Edit target roles/cities/CTC/etc. | `/edit` link (from email) or dashboard config panel |

You do not need to SSH into the server for normal operation. That's only
needed for deeper maintenance (e.g. this handoff, code updates).

## Known limitation: occasional login/verification prompts

Naukri (via Akamai's bot-detection) occasionally flags cloud server IPs
(AWS/GCP/any datacenter, not specific to this deployment) and asks for a
fresh login or a human verification click. This is a platform-wide
behavior of any cloud-hosted automation, not a bug in this bot. It's
already handled gracefully:
- The bot detects it, pauses just that one search, and keeps going.
- If the whole session expires, you get one email (throttled to avoid
  spam) with a one-tap phone link to log back in — takes ~30 seconds.

**Optional upgrade for near-zero interruptions:** a residential proxy
(~$5–15/month) eliminates most of these prompts entirely. This is a config
toggle, not a code change, if you'd like it enabled later.

## Architecture summary

- `app.py` — Flask control server (start/stop, pause/resume, login/session
  webhooks, JSON API, dashboard hosting), token-gated for security.
- `bot.py` — Playwright automation: search, AI scoring, apply, form-fill.
- `ai_helper.py` — Groq LLM integration for scoring/summarizing/answering.
- `dashboard/` — React/Vite live status dashboard.
- `Caddyfile` / `docker-compose.yml` — HTTPS reverse proxy + container
  orchestration, auto-restart on crash/reboot.

Full technical details, setup instructions, and security notes are in
`README.md` in the repository.

## Security

- All control routes require a per-deployment secret token (embedded
  automatically in your email links) — no unauthenticated access to
  start/stop/apply actions.
- Your Naukri password never touches the server — only session cookies,
  captured via a real browser you log into yourself.
- The dashboard (read-only stats) is safe to be public; no credentials or
  controls are exposed there.

## Maintenance going forward

- The stack auto-recovers from crashes and EC2 reboots
  (`restart: unless-stopped` on both containers, Docker enabled at boot).
- Occasional login refresh (above) is the only expected manual touchpoint.
- Code lives at: `https://github.com/vidur0001/naukri-jobs-auto-applier`
  (private repo).
