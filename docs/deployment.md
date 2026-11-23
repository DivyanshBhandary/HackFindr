# Deployment and cost plan

## 1. Where HackFindr runs

| Option | Cost | Good for | Watch out for |
|---|---|---|---|
| **Laptop + HTTPS tunnel** (Cloudflare Tunnel or ngrok) | Free | The hackathon demo | The laptop must stay on and online. Everything (database, Gmail, keys) stays on the laptop. |
| **Small VPS** (1 vCPU, 1 GB RAM) with a domain and HTTPS | Roughly $4–7 a month | A real launch for a few thousand students | You keep it updated. Back up `data/hackfindr.db`. |
| **Hugging Face Spaces** (the README already has the Space settings, `app.py` is the entry file) | Free CPU; persistent storage is a paid add-on | A public demo link without a laptop | Without persistent storage, accounts reset on every restart. Email ports may be blocked, so reminders need an HTTP email service (Resend, SendGrid) there. |

**For the hackathon:** laptop + tunnel. **After the hackathon:** a small VPS. SQLite handles thousands of
users on one server. Move to Postgres only when you need more than one server.

### Settings for any host (`.env` or the host's secrets)

| Variable | What it is |
|---|---|
| `GEMINI_API_KEY` | Gemma 4 through the Gemini API |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in. Add `https://<your address>/auth` as a redirect URI in Google Cloud Console |
| `HACKFINDR_PUBLIC_URL` | The public HTTPS address, e.g. `https://hackfindr.example.com` (used for sign-in, email links, QR code) |
| `HACKFINDR_PUBLIC=1` | Website mode: visitor limits, background event refresh every 6 hours, no manual fetching |
| `HACKFINDR_DATA_DIR` | A folder that survives restarts (events, caches) |
| `SMTP_USER`, `SMTP_PASSWORD` | Gmail address + App password for reminder emails |
| `SESSION_SECRET`, `STATS_KEY` | Optional; made automatically in `data/` if missing |

## 2. Gemma cost

Price used below: about **$0.13 per million input tokens and $0.40 per million output tokens** for
`gemma-4-26b-a4b-it`. This figure comes from a third-party price list and is **not confirmed**; check
[Google's pricing page](https://ai.google.dev/gemini-api/docs/pricing) before quoting it. A free tier with rate limits
may also apply. Token sizes are measured from HackFindr's real prompts (about 4 characters per token).

### Per event (paid once, shared by every student)

| Gemma job | When | Tokens in / out (approx.) |
|---|---|---|
| AI summary | The first time anyone opens the event | 700 / 250 |
| Eligibility rules | Once, 8 events per call | 320 / 120 per event |

**1,000 new events ≈ 1.0 M tokens in, 0.4 M out ≈ $0.30.** Both results are cached (`summary_cache.json`,
`eligibility_cache.json`), so they are never paid for twice.

### Per active student per day

| Gemma job | Assumed use | Tokens in / out |
|---|---|---|
| Best fit (ranks 25 events) | 1 a day: cached per student per day | 2,200 / 750 |
| "Is this for me?" | 2 | 1,400 / 300 |
| Event chat | 3 questions | 2,400 / 250 |
| **Total** |  | **≈ 6,000 / 1,300** |

**1,000 daily active students ≈ 6 M tokens in + 1.3 M out ≈ $1.30 a day ≈ $40 a month.**
That is about **4 paise ($0.0013) per student per day**.

### Why it stays cheap

- **Everything shared is cached:** summaries and eligibility rules are made once per event, not once per student.
- **Best fit is cached per student per day**, and plain code first narrows ~900 events down to the 25 worth ranking.
- **Matching is plain code:** checking "can I join?" against every card costs nothing; Gemma only reads the rules once.
- **Repeated questions hit a disk cache** in `gemma_client.py`.
- **If Gemma is busy**, Best fit falls back to a keyword match instead of failing.
- **Website mode limits** each visitor to 30 AI calls an hour and the whole site to `AI_PER_DAY` (500 by default,
  in `app_template/app.py`). Raise `AI_PER_DAY` to about 10× the expected daily students.

### Email cost

Gmail (free) allows about 500 emails a day, which is enough for the demo and early users. Beyond that, an email
service such as Resend or SendGrid has a free tier and then charges per email.

## 3. Privacy and consent

- Sign-in is Google only; HackFindr never sees a password. The session cookie holds only the user id.
- Profile fields are optional and used only for matching. **Delete my account** removes the profile, saved events,
  fit checks and email history at once.
- Emails are opt-in, and every email has a signed one-click unsubscribe link.
- Usage stats store no names, emails or IP addresses: visitors are counted with a hash that changes every day.
  Feedback is anonymous.
- Personal data (`data/`) is git-ignored and never pushed.
