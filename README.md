# Hollywood Movie Clips — 24-Hour YouTube & Facebook Upload Workflow

Automated pipeline that uploads **5 Hollywood movie clips per 24 hours** to both
YouTube (Shorts) and Facebook (Reels) with fully independent scheduling,
duplicate-upload prevention, per-agent retries, and a detailed Discord report
after every upload job.

## Architecture

```
Movie Clips / Ready  ──►  Agent 1: Drive Fetch  ──►  Agent 2: SEO (American English)
                                                          │
                                                          ▼
                                          Upload Queue & Scheduler (5 slots/day)
                                                          │
                                        ┌─────────────────┴─────────────────┐
                                        ▼                                   ▼
                              Agent 3: YouTube Upload          Agent 4: Facebook Upload
                                        │                                   │
                                        └─────────────┬─────────────────────┘
                                                      ▼
                                            Upload Verification
                                                      ▼
                                   Move clip to "Movie Clips / Uploaded"
                                                      ▼
                                    Agent 5: Discord Final Report
```

| Agent | Responsibility |
| ----- | -------------- |
| **Agent 1** — Drive Fetch | Lists the `Movie Clips / Ready` folder, picks the next new clip, moves it to `Processing`, downloads it locally. |
| **Agent 2** — Movie Clip Analysis & SEO | Derives the movie title, builds search keywords, YouTube SEO title/description, Facebook caption, hashtags and metadata — all in natural American English (US audience). |
| **Agent 3** — YouTube Upload | Uploads a Short with correct title, description, hashtags and metadata; verifies the upload and returns the public URL. |
| **Agent 4** — Facebook Upload | Uploads a Reel/video with the American English caption; verifies success and returns the public URL. |
| **Agent 5** — Discord Report | Sends the final detailed status report (with GitHub Actions run URL) to Discord. |

## Scheduling

The schedule lives in `config/schedule.json`. It defines **5 slots per day**, each
with an independent YouTube time and Facebook time (timezone configurable in
`config/settings.yaml`, default `America/New_York`). GitHub Actions runs the
workflow every 15 minutes; the orchestrator only uploads when a slot is due, so
all 5 videos go live at staggered, never-identical times.

| Video | YouTube  | Facebook |
| ----- | -------- | -------- |
| #1    | 05:00    | 05:15    |
| #2    | 09:00    | 09:15    |
| #3    | 13:00    | 13:15    |
| #4    | 17:00    | 17:15    |
| #5    | 21:00    | 21:15    |

Each platform's timing is managed independently (see the per-platform `*_time`
fields). YouTube and Facebook uploads for the same clip can even use different
slot times.

## Duplicate-upload prevention

- A clip is uploaded **only once**: it is moved `Ready -> Processing -> Uploaded`.
- Once both platform uploads succeed, the file is moved to `Movie Clips / Uploaded`
  and is never detected again by Agent 1.
- If an upload fails, the file **stays in `Processing`** and is retried on later
  runs (up to `max_retries_per_platform`, default 3).
- If YouTube succeeds but Facebook fails, **only Facebook is retried** — YouTube
  is never re-uploaded. Persisted state in `state/workflow_state.json` tracks
  per-platform status, retry counts, URLs and moved flags.

## Error handling

Every failure is reported to Discord (and logged), never silently swallowed:

- Drive file missing / empty Ready folder → Discord error report (once per slot/day)
- Download/fetch failure → retried, then reported
- Processing failure → error report
- YouTube failure → retried, then status report
- Facebook failure → retried, then status report (independent of YouTube)
- Missing URL → failure report
- Drive move to `Uploaded` failed → **critical warning** in the report

## Discord final report

After each completed job, Agent 5 posts an embed with:

- Video number, movie/clip name, upload status
- YouTube status + public URL
- Facebook status + public URL
- Google Drive source file + whether it was moved to `Uploaded`
- Upload time, workflow status
- **GitHub Actions workflow run status + direct run URL** (opens the exact run in the UI)

## Setup

### 1. Repository secrets

Create the following secrets in **Settings → Secrets and variables → Actions**:

| Secret | Purpose |
| ------ | ------- |
| `GOOGLE_DRIVE_SERVICE_ACCOUNT` | Service-account JSON (or base64) with Drive access. Share the `Movie Clips` folder with the SA email. |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN` | OAuth2 credentials for the YouTube channel (see below). |
| `FACEBOOK_PAGE_ID` / `FACEBOOK_PAGE_ACCESS_TOKEN` | Page token with `pages_manage_posts` + `pages_read_engagement`. |
| `DISCORD_WEBHOOK_URL` | Webhook URL for the report channel. |
| `USER_LLM_API_KEY` *(optional)* | Your own LLM key for richer SEO; omit to use the built-in rule-based SEO. |

### 2. Google Drive folder structure

```
Movie Clips/
├── Ready/        # drop new clips here
├── Processing/   # auto-managed
└── Uploaded/     # auto-managed (dedup)
```

Folders are resolved by path from `config/settings.yaml`; override with explicit
folder IDs if desired.

### 3. YouTube OAuth2 refresh token

```bash
pip install -r requirements.txt
python -c "
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file('client_secret.json',
    ['https://www.googleapis.com/auth/youtube.upload'])
creds = flow.run_local_server(port=0)
print('REFRESH_TOKEN:', creds.refresh_token)
"
```

### 4. Enable the workflow

Push to `main`. The workflow runs on a cron (`*/15 * * * *`) and on
`workflow_dispatch` (manual). Open **Actions → Hollywood Clips 24h Upload
Workflow → Run workflow** to trigger an immediate check.

## Local testing

```bash
pip install -r requirements.txt
python -m src.main --dry-run --force
```

This simulates the full pipeline (fetch a fake clip → SEO → upload → move →
Discord report) without touching Google Drive, YouTube, Facebook or Discord.

## Project layout

```
.github/workflows/hollywood-clips-upload.yml   # cron schedule + secrets wiring
config/settings.yaml                          # app/drive/youtube/facebook/discord/seo
config/schedule.json                          # 5 slots/day, per-platform times
config/seo_profiles.json                      # broad Hollywood search strategy
src/agents/agent1_drive_fetch.py              # Agent 1
src/agents/agent2_seo.py                      # Agent 2
src/agents/agent3_youtube.py                  # Agent 3
src/agents/agent4_facebook.py                 # Agent 4
src/agents/agent5_discord.py                  # Agent 5
src/orchestrator.py                           # scheduling queue + retries + dedup
src/common/                                   # config, state, drive, time utils
state/workflow_state.json                     # persisted job/retry/URL state
```

## Legal note

Only upload clips you have the rights to distribute. Respect copyright and
platform terms of service.
