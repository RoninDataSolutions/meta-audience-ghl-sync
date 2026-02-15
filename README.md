# GHL to Meta Custom Audience Sync

Docker-based application that syncs GoHighLevel Smart List contacts to Meta (Facebook) Custom Audiences with LTV-based value optimization. Normalizes customer lifetime values using Claude AI, creates value-based Custom Audiences, and auto-generates 1% Lookalike Audiences.

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your credentials (see setup guides below)

# 2. Build and run
docker-compose up -d

# 3. Access dashboard
open http://your-server-ip:9876
```

## Setup Guides

### Meta System User Token

**Prerequisites:** You need a Meta App linked to your Business. If you don't have one:

1. Go to [Meta for Developers](https://developers.facebook.com/) → **My Apps** → **Create App**
2. Select **Other** → **Business** → pick your Business account
3. Give it a name (e.g., "GHL Audience Sync") and create it
4. No need to configure any products — you just need the app to exist

**Create System User & Generate Token:**

1. Go to [Meta Business Suite](https://business.facebook.com/) → **Business Settings**
2. Navigate to **Users → System Users**
3. Click **Add** → name it (e.g., "GHL Sync Bot") → set role to **Admin** → Create
4. Click **Add Assets**:
   - Select **Apps** → find your app → toggle **Full Control** → Save
   - Select **Ad Accounts** → find your ad account → toggle **Full Control** → Save
5. Click **Generate New Token**
6. **Select your App** from the dropdown (this is the step that fails if no app is assigned)
7. Check these permissions:
   - `ads_management`
   - `ads_read`
   - `business_management`
8. Click **Generate Token** → copy the token
9. Add to `.env` as `META_ACCESS_TOKEN`
10. Set `META_AD_ACCOUNT_ID` to your ad account ID (format: `act_123456789`)
11. Set `META_BUSINESS_ID` to your business ID (visible in Business Settings URL)

### GHL Private Integration Token (API v2)

This app uses the **GHL v2 API** via a Private Integration token. When creating the integration, select these scopes:

- **Contacts** — read contacts and smart lists
- **Custom Fields** — read custom field definitions to select the LTV field

**Steps:**

1. Login to GoHighLevel → open your **sub-account**
2. Go to **Settings → Integrations → Private Integrations**
3. Click **Create Private Integration**
4. Name it (e.g., "Meta Audience Sync")
5. Select the required scopes: **Contacts (Read)**, **Custom Fields (Read)**
6. Click **Create** → **copy the token immediately** (it cannot be retrieved later)
7. Add to `.env` as `GHL_API_KEY`
8. Find your **Location ID**: look at the URL when inside the sub-account — it's the ID after `/location/` (e.g., `https://app.gohighlevel.com/v2/location/abc123XYZ/...` → `abc123XYZ`)
9. Add to `.env` as `GHL_LOCATION_ID`

> **Note:** Private Integration tokens are static (no refresh needed) but should be rotated every 90 days. The token is scoped to the sub-account where it was created.

### Claude API Key

1. Go to [Anthropic Console](https://console.anthropic.com/)
2. Create an API key
3. Add to `.env` as `CLAUDE_API_KEY`

### PostgreSQL

The app connects to an external PostgreSQL instance. Tables are auto-created on first startup.

```
POSTGRES_HOST=your-postgres-host
POSTGRES_PORT=5432
POSTGRES_DB=ghl_meta_sync
POSTGRES_USER=your_user
POSTGRES_PASSWORD=your_password
```

### Email (Optional)

Configure SMTP for sync success/failure notifications:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_FROM_EMAIL=sync@yourdomain.com
SMTP_TO_EMAIL=you@yourdomain.com
```

## How It Works

1. **Daily at 2 AM** (configurable via `SYNC_SCHEDULE_CRON`), or manually via the dashboard
2. Fetches all contacts from the configured GHL Smart List
3. Extracts LTV values from the selected custom field
4. Sends LTV values to Claude API for percentile normalization (0-100)
5. Hashes all PII (SHA256) and prepares data for Meta
6. Creates a new Custom Audience (`GHL-HighValue-YYYY-MM-DD`)
7. Uploads contacts with normalized values in batches of 10,000
8. Creates a 1% Lookalike Audience (US) from the Custom Audience
9. Sends email notification with results

## Architecture

- **Backend**: Python FastAPI serving on port 9876
- **Frontend**: React + Vite (built to static files, served by FastAPI)
- **Database**: PostgreSQL (external)
- **Scheduler**: APScheduler (in-process cron)
- **Container**: Single Docker container

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GHL_API_KEY` | Yes | — | GoHighLevel API key |
| `GHL_LOCATION_ID` | Yes | — | GHL location/sub-account ID |
| `META_ACCESS_TOKEN` | Yes | — | Meta System User token |
| `META_AD_ACCOUNT_ID` | Yes | — | Meta Ad Account ID (act_xxx) |
| `META_BUSINESS_ID` | Yes | — | Meta Business ID |
| `CLAUDE_API_KEY` | Yes | — | Anthropic API key |
| `POSTGRES_HOST` | Yes | — | PostgreSQL host |
| `POSTGRES_PORT` | No | 5432 | PostgreSQL port |
| `POSTGRES_DB` | No | ghl_meta_sync | Database name |
| `POSTGRES_USER` | Yes | — | Database user |
| `POSTGRES_PASSWORD` | Yes | — | Database password |
| `SMTP_HOST` | No | — | SMTP server host |
| `SMTP_PORT` | No | 587 | SMTP port |
| `SMTP_USERNAME` | No | — | SMTP username |
| `SMTP_PASSWORD` | No | — | SMTP password |
| `SMTP_FROM_EMAIL` | No | — | Sender email |
| `SMTP_TO_EMAIL` | No | — | Recipient email |
| `SYNC_SCHEDULE_CRON` | No | `0 2 * * *` | Cron schedule |
| `WEB_PORT` | No | 9876 | Web UI port |
| `LOG_LEVEL` | No | INFO | Logging level |

## Unraid Deployment

```yaml
# docker-compose.yml maps:
# Port: 9876 → 9876
# Logs: ./logs → /app/logs (map to /mnt/user/appdata/ghl-meta-sync/logs)
# Network: unraid-network (for PostgreSQL access)
```

## Development

```bash
# Backend
cd backend
source venv/bin/activate
python3 app.py

# Frontend (separate terminal)
cd frontend
npm install
npm run dev  # Proxies /api to localhost:9876
```
