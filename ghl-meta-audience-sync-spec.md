# GHL to Meta Custom Audience Sync - Technical Specification

## Project Overview

Build a Docker-based application that syncs GoHighLevel (GHL) Smart List contacts to Meta (Facebook) Custom Audiences with LTV-based value optimization. The application normalizes customer lifetime values using Claude AI, creates value-based Custom Audiences in Meta, and automatically generates Lookalike Audiences.

## Core Features

### 1. Data Synchronization
- Pull contacts from selected GHL Smart List via API
- Normalize LTV values to 0-100 percentile ranking using Claude API
- Push contacts with normalized values to Meta Custom Audiences API
- Auto-create 1% Lookalike Audience (US) after Custom Audience creation
- Full sync (replace entire audience) on each run
- Daily scheduled sync via cron
- Track sync history and changes

### 2. User Interface
- Web-based dashboard accessible on port 9876
- Configure Smart List selection from GHL
- Select custom LTV field from available GHL custom fields
- View last sync status and timestamp
- Manual "Sync Now" button
- Sync history log with details (contacts synced, added, removed, errors)
- Contact count and value distribution visualization
- Email notification settings

### 3. Value Normalization Strategy
- Use Claude API to bucket customers into percentiles (0-100)
- Percentile calculation across ALL contacts in Smart List
- No floor or ceiling on LTV values
- Higher percentile = higher value customer
- Top 1% gets value 100, bottom 1% gets value 0-1

## Architecture

### Technology Stack
- **Backend**: Python (FastAPI or Flask)
- **Frontend**: Your choice (React/Vue/Svelte/vanilla JS - optimize for simplicity and maintainability)
- **Database**: PostgreSQL (self-hosted on Unraid)
- **Scheduler**: APScheduler or similar for daily cron
- **Containerization**: Single Docker container
- **Email**: SMTP

### Docker Configuration
```dockerfile
# Single container with:
# - Python backend + web server
# - Frontend static files served by backend
# - PostgreSQL client (connects to external Postgres)
# - Exposed port: 9876
```

### Database Schema

#### `sync_configs` table
- `id` (primary key)
- `ghl_smart_list_id` (string)
- `ghl_smart_list_name` (string)
- `ghl_ltv_field_name` (string) - custom field name in GHL
- `meta_ad_account_id` (string)
- `sync_enabled` (boolean)
- `created_at` (timestamp)
- `updated_at` (timestamp)

#### `sync_runs` table
- `id` (primary key)
- `config_id` (foreign key to sync_configs)
- `started_at` (timestamp)
- `completed_at` (timestamp)
- `status` (enum: 'running', 'success', 'failed')
- `contacts_processed` (integer)
- `contacts_matched` (integer) - Meta's match count
- `meta_audience_id` (string)
- `meta_audience_name` (string) - format: "GHL-HighValue-YYYY-MM-DD"
- `meta_lookalike_id` (string)
- `meta_lookalike_name` (string)
- `error_message` (text, nullable)
- `normalization_stats` (jsonb) - stores min/max/median LTV, percentile distribution

#### `sync_contacts` table
- `id` (primary key)
- `sync_run_id` (foreign key to sync_runs)
- `ghl_contact_id` (string)
- `email` (string, nullable)
- `phone` (string, nullable)
- `first_name` (string, nullable)
- `last_name` (string, nullable)
- `raw_ltv` (decimal)
- `normalized_value` (integer) - 0-100 percentile
- `meta_matched` (boolean)
- `created_at` (timestamp)

## Environment Variables

```bash
# GHL Configuration
GHL_API_KEY=your_ghl_api_key
GHL_LOCATION_ID=your_ghl_location_id

# Meta Configuration
META_ACCESS_TOKEN=your_meta_system_user_token
META_AD_ACCOUNT_ID=act_123456789
META_BUSINESS_ID=your_business_id

# Claude Configuration
CLAUDE_API_KEY=your_anthropic_api_key

# Database Configuration
POSTGRES_HOST=your_unraid_postgres_host
POSTGRES_PORT=5432
POSTGRES_DB=ghl_meta_sync
POSTGRES_USER=your_db_user
POSTGRES_PASSWORD=your_db_password

# Email Configuration
SMTP_HOST=smtp.your-provider.com
SMTP_PORT=587
SMTP_USERNAME=your_smtp_username
SMTP_PASSWORD=your_smtp_password
SMTP_FROM_EMAIL=sync-app@yourdomain.com
SMTP_TO_EMAIL=you@yourdomain.com

# Application Configuration
SYNC_SCHEDULE_CRON=0 2 * * *  # Daily at 2 AM
WEB_PORT=9876
LOG_LEVEL=INFO
```

## API Integration Details

### GoHighLevel API

**Authentication:**
- Use API Key in Authorization header: `Authorization: Bearer {GHL_API_KEY}`
- Base URL: `https://rest.gohighlevel.com/v1/`

**Key Endpoints:**

1. **Get Smart Lists**
```
GET /contacts/smart-lists/{locationId}
```

2. **Get Contacts from Smart List**
```
GET /contacts/smart-list/{smartListId}/contacts
```

3. **Get Custom Fields**
```
GET /custom-fields/{locationId}
```

**Contact Data to Extract:**
- email
- phone
- firstName
- lastName
- country
- city
- state
- postalCode
- dateOfBirth
- customField[{ltv_field_name}] - the LTV value

### Meta Marketing API

**Authentication:**
- Use System User Token (long-lived, doesn't expire in 60 days)
- Instructions for obtaining System User Token should be in README

**Base URL:** `https://graph.facebook.com/v21.0/`

**Key Endpoints:**

1. **Create Custom Audience**
```
POST /act_{ad_account_id}/customaudiences
Body:
{
  "name": "GHL-HighValue-2026-02-15",
  "subtype": "CUSTOM",
  "description": "Synced from GHL Smart List on 2026-02-15",
  "customer_file_source": "USER_PROVIDED_ONLY"
}
```

2. **Add Users to Audience (with LTV)**
```
POST /{custom_audience_id}/users
Body:
{
  "payload": {
    "schema": ["EMAIL", "PHONE", "FN", "LN", "CT", "ST", "ZIP", "COUNTRY", "VALUE"],
    "data": [
      ["hash_email", "hash_phone", "hash_fn", "hash_ln", "hash_city", "hash_state", "hash_zip", "us", 85],
      // ... more contacts
    ]
  }
}
```

**Important Notes:**
- All PII must be SHA256 hashed, lowercase, trimmed
- Phone numbers: E.164 format (+1234567890)
- VALUE field: 0-100 integer (our normalized percentile)
- Send in batches of 10,000 contacts max

3. **Create Lookalike Audience**
```
POST /act_{ad_account_id}/customaudiences
Body:
{
  "name": "GHL-HighValue-2026-02-15-LAL-1%",
  "subtype": "LOOKALIKE",
  "origin_audience_id": "{custom_audience_id}",
  "lookalike_spec": {
    "ratio": 0.01,
    "country": "US",
    "type": "similarity"
  }
}
```

### Claude API (Anthropic)

**Purpose:** Percentile bucketing of LTV values

**Endpoint:** `https://api.anthropic.com/v1/messages`

**Request:**
```json
{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 4096,
  "messages": [{
    "role": "user",
    "content": "I have {count} customers with LTV values ranging from ${min} to ${max}. Here are the LTV values: {ltv_array}. Calculate the percentile rank (0-100) for each value where 100 = highest value, 0 = lowest value. Return ONLY a JSON array of percentile integers in the same order as input, no explanation."
  }]
}
```

**Expected Response:**
```json
{
  "content": [{
    "type": "text",
    "text": "[0, 15, 23, 45, 67, 89, 95, 100, ...]"
  }]
}
```

**Error Handling:**
- If Claude API fails, send email notification
- Log error details
- Skip sync run (do not proceed with un-normalized values)

## Workflow: Daily Sync Process

```
1. Cron triggers daily sync (2 AM default)
2. Create new sync_run record (status: 'running')
3. Fetch Smart List contacts from GHL
4. Extract LTV values from custom field
5. Send LTV array to Claude API for percentile normalization
6. Receive normalized 0-100 values
7. Prepare contact data (hash all PII fields)
8. Create new Meta Custom Audience "GHL-HighValue-{date}"
9. Upload contacts with normalized values to Meta (batches of 10k)
10. Wait for Meta to process (poll status)
11. Create 1% Lookalike Audience (US)
12. Update sync_run record (status: 'success', IDs, stats)
13. Store contact details in sync_contacts table
14. Send success email notification
```

**Error Handling at Each Step:**
- Any failure: update sync_run status to 'failed'
- Log error_message
- Send email notification with error details
- Halt execution

## UI Requirements

### Dashboard Page (`/`)

**Header:**
- App title: "GHL → Meta Audience Sync"
- Last sync status badge (success/failed/running)
- Last sync timestamp
- "Sync Now" button (manual trigger)

**Configuration Section:**
- Dropdown: Select GHL Smart List (fetched from GHL API)
- Dropdown: Select LTV Custom Field (fetched from GHL custom fields API)
- Save Configuration button

**Current Status Section:**
- Active Meta Ad Account ID
- Current Custom Audience (if exists)
- Current Lookalike Audience (if exists)
- Contact count in last sync
- Meta match rate percentage

**Sync History Table:**
- Columns: Date, Status, Contacts Processed, Contacts Matched, Match Rate, Audience Created, Lookalike Created, Duration, Actions
- Actions: View Details, View Errors (if failed)
- Paginated (20 per page)
- Sort by date descending

**Value Distribution Chart:**
- Simple bar chart showing percentile distribution
- X-axis: Percentile buckets (0-10, 10-20, ... 90-100)
- Y-axis: Number of contacts
- Display from last successful sync

**Email Settings:**
- Current SMTP FROM and TO emails (read from ENV, display only)
- Test Email button (sends test notification)

### Sync Details Modal (`/sync/{sync_run_id}`)

**When clicking "View Details" in history table:**
- Sync Run ID
- Started/Completed timestamps
- Duration
- Status
- Contacts processed/matched
- Meta Audience ID and name
- Lookalike Audience ID and name
- Normalization stats (min/max/median LTV, percentile ranges)
- Error message (if failed)
- Sample of contacts (first 10) with raw LTV → normalized value

### API Endpoints (Backend)

```
GET  /api/smart-lists          # Fetch GHL Smart Lists
GET  /api/custom-fields        # Fetch GHL Custom Fields
GET  /api/config               # Get current sync config
POST /api/config               # Save sync config
POST /api/sync/trigger         # Manual sync trigger
GET  /api/sync/history         # Get sync run history
GET  /api/sync/{id}            # Get specific sync run details
GET  /api/sync/status          # Get current sync status (for polling)
POST /api/email/test           # Send test email
```

## Email Notifications

### Success Email Template

**Subject:** ✅ GHL Meta Sync Successful - {date}

**Body:**
```
Sync completed successfully at {timestamp}

📊 Summary:
- Contacts Processed: {count}
- Contacts Matched by Meta: {matched} ({match_rate}%)
- Custom Audience: {audience_name} (ID: {audience_id})
- Lookalike Audience: {lookalike_name} (ID: {lookalike_id})

🎯 Value Distribution:
- Top 10%: {count} contacts
- Middle 50%: {count} contacts
- Bottom 40%: {count} contacts

View full details: http://your-server:9876/sync/{sync_run_id}
```

### Failure Email Template

**Subject:** ❌ GHL Meta Sync Failed - {date}

**Body:**
```
Sync failed at {timestamp}

❌ Error: {error_message}

Failed at step: {step_name}

Details:
- Smart List: {smart_list_name}
- LTV Field: {ltv_field_name}
- Contacts Retrieved: {count}

View logs: http://your-server:9876/sync/{sync_run_id}

Please check the application and retry manually.
```

## Docker Deployment

### Dockerfile
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 9876

# Run application
CMD ["python", "app.py"]
```

### docker-compose.yml (for Unraid)
```yaml
version: '3.8'

services:
  ghl-meta-sync:
    build: .
    container_name: ghl-meta-sync
    ports:
      - "9876:9876"
    environment:
      # All ENV vars from .env file
      - GHL_API_KEY=${GHL_API_KEY}
      - GHL_LOCATION_ID=${GHL_LOCATION_ID}
      - META_ACCESS_TOKEN=${META_ACCESS_TOKEN}
      - META_AD_ACCOUNT_ID=${META_AD_ACCOUNT_ID}
      - META_BUSINESS_ID=${META_BUSINESS_ID}
      - CLAUDE_API_KEY=${CLAUDE_API_KEY}
      - POSTGRES_HOST=${POSTGRES_HOST}
      - POSTGRES_PORT=${POSTGRES_PORT}
      - POSTGRES_DB=${POSTGRES_DB}
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - SMTP_HOST=${SMTP_HOST}
      - SMTP_PORT=${SMTP_PORT}
      - SMTP_USERNAME=${SMTP_USERNAME}
      - SMTP_PASSWORD=${SMTP_PASSWORD}
      - SMTP_FROM_EMAIL=${SMTP_FROM_EMAIL}
      - SMTP_TO_EMAIL=${SMTP_TO_EMAIL}
      - SYNC_SCHEDULE_CRON=${SYNC_SCHEDULE_CRON:-0 2 * * *}
      - WEB_PORT=${WEB_PORT:-9876}
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
    volumes:
      - ./logs:/app/logs  # Persistent logs on Unraid
    restart: unless-stopped
    networks:
      - unraid-network

networks:
  unraid-network:
    external: true
```

### Volume Mounts on Unraid
- `/mnt/user/appdata/ghl-meta-sync/logs` → `/app/logs` (logs persistence)
- Postgres connects via network (no volume needed)

## README Instructions

### Meta System User Token Setup

Include step-by-step instructions:

1. Go to Meta Business Suite → Business Settings
2. Navigate to Users → System Users
3. Click "Add" and create a new System User
4. Assign the System User to your Ad Account with "Admin" access
5. Generate a token with these permissions:
   - `ads_management`
   - `ads_read`
   - `business_management`
6. Copy the token (it won't expire unless regenerated)
7. Add to `.env` file as `META_ACCESS_TOKEN`

### GHL API Key Setup

1. Login to GHL
2. Go to Settings → API
3. Generate new API key with full permissions
4. Copy to `.env` file as `GHL_API_KEY`
5. Find Location ID in URL when inside a sub-account

### Initial Setup

```bash
# 1. Clone repository
git clone [repo-url]
cd ghl-meta-sync

# 2. Create .env file
cp .env.example .env
# Edit .env with your credentials

# 3. Build and run Docker container
docker-compose up -d

# 4. Access UI
http://your-unraid-ip:9876

# 5. Configure sync
- Select Smart List
- Select LTV custom field
- Click Save Configuration

# 6. Test sync
Click "Sync Now" button

# 7. Monitor
Check email for notifications
View sync history in UI
```

## Testing Checklist

- [ ] GHL API connection (fetch Smart Lists)
- [ ] GHL API connection (fetch Custom Fields)
- [ ] GHL API connection (fetch contacts from Smart List)
- [ ] Claude API normalization (verify percentile calculation)
- [ ] Meta API connection (create Custom Audience)
- [ ] Meta API connection (upload contacts with values)
- [ ] Meta API connection (create Lookalike Audience)
- [ ] Database writes (sync_runs, sync_contacts)
- [ ] Email notifications (success)
- [ ] Email notifications (failure)
- [ ] UI displays Smart Lists correctly
- [ ] UI displays Custom Fields correctly
- [ ] UI displays sync history correctly
- [ ] UI chart renders value distribution
- [ ] Manual sync trigger works
- [ ] Scheduled daily sync works
- [ ] Error handling (GHL API failure)
- [ ] Error handling (Meta API failure)
- [ ] Error handling (Claude API failure)
- [ ] Docker build succeeds
- [ ] Docker container runs on port 9876
- [ ] Postgres connection works
- [ ] Logs persist to Unraid volume

## Security Considerations

**Note:** As per requirements, minimal security for v1:
- All credentials stored in ENV variables
- No encryption at rest
- Basic input validation on API endpoints
- No authentication on web UI (assumes local network access)

**Future Enhancements (not in v1):**
- Add basic auth to UI
- Encrypt sensitive fields in database
- Implement RBAC
- Add audit logging
- PII redaction in logs

## Performance Optimization

- Batch Meta API uploads (10k contacts per request)
- Use connection pooling for Postgres
- Cache GHL Smart Lists/Custom Fields (5 min TTL)
- Async processing for long-running sync operations
- Implement retry logic with exponential backoff for API failures

## Monitoring & Logging

- Log all API requests/responses (DEBUG level)
- Log sync progress milestones (INFO level)
- Log all errors with stack traces (ERROR level)
- Store logs in `/app/logs/app.log`
- Rotate logs daily (keep 30 days)
- Include request IDs in all logs for tracing

## Known Limitations

1. Full sync only (no incremental updates) - v1 design choice
2. Single Meta Ad Account support - v1 design choice
3. US-only Lookalike Audiences - configurable in future
4. 1% Lookalike only - configurable in future
5. No multi-tenancy - single GHL location only
6. No authentication on UI - local network deployment assumption

## Future Enhancements (Out of Scope for v1)

- Incremental sync support
- Multi-country Lookalike support
- Variable Lookalike percentage
- Multiple Meta Ad Accounts
- Claude-powered LTV insights/analysis
- Slack notifications
- Webhook support
- Multi-tenancy (multiple GHL locations)
- Advanced filtering (min/max LTV thresholds)
- Custom audience exclusion rules
- A/B testing framework for audiences

---

## Development Notes for Claude Code

### Priority Order
1. Database schema and migrations
2. Backend API framework and routes
3. GHL API integration
4. Meta API integration
5. Claude API integration
6. Sync orchestration logic
7. Email notifications
8. Frontend UI
9. Docker containerization
10. Documentation (README, setup guides)

### Key Libraries to Use
- **Backend**: FastAPI (modern, async, auto-docs)
- **Database**: SQLAlchemy ORM, Alembic for migrations
- **Scheduling**: APScheduler
- **HTTP Requests**: httpx (async support)
- **Hashing**: hashlib (SHA256 for PII)
- **Email**: smtplib or python-emailer
- **Frontend**: Consider React + Vite or vanilla JS with a simple framework
- **Charts**: Chart.js or similar lightweight library

### Code Structure
```
/app
  /api
    __init__.py
    ghl.py          # GHL API client
    meta.py         # Meta API client
    claude.py       # Claude API client
  /models
    __init__.py
    database.py     # SQLAlchemy models
  /services
    __init__.py
    sync.py         # Sync orchestration logic
    normalizer.py   # Claude-based normalization
    email.py        # Email notification service
  /routes
    __init__.py
    config.py       # Config endpoints
    sync.py         # Sync endpoints
  /frontend
    /static
      /css
      /js
    index.html
  /migrations      # Alembic migrations
  config.py        # App configuration (loads ENV)
  scheduler.py     # Cron scheduler setup
  app.py           # Main FastAPI app
  requirements.txt
  Dockerfile
  docker-compose.yml
  .env.example
  README.md
```

### Error Handling Philosophy
- Fail fast and loud (send email immediately on error)
- Never proceed with un-normalized values
- Validate all ENV variables on startup
- Use try/except at every external API call
- Return meaningful error messages to UI

### Testing Approach
- Manual testing for v1 (no unit tests required initially)
- Provide test scripts for each API integration
- Include "Test Connection" buttons in UI for each service

---

**End of Specification**

This spec should provide everything needed to build the application. Let me know if you need any clarifications!
