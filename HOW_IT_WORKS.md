# GHL → Meta Audience Sync

This app automatically syncs your GoHighLevel (GHL) contacts into a Meta (Facebook/Instagram) Custom Audience, ranked by customer lifetime value (LTV). Meta then uses that audience to build a Lookalike Audience — finding new people who look like your best customers.

---

## What problem does it solve?

You have customer data in GoHighLevel with LTV values. Meta needs that data — hashed and value-ranked — to build high-quality ad audiences. Doing this manually is slow, error-prone, and requires technical knowledge. This app automates it completely, running daily at 2 AM.

---

## How it works (step by step)

### 1. Fetch contacts from GHL
The app pulls all contacts from your configured GHL Smart List via the GHL v2 API. Contacts with no email and no phone are skipped — Meta can't match them anyway (e.g. Instagram-only leads with no contact info).

### 2. Extract LTV values
Each contact's LTV value is read from a custom field you configure (e.g. `contact.ltv`). Contacts with no LTV value are assigned `0` — they still get included in the audience, which helps Meta find more people to match.

### 3. Normalize LTV to percentiles (0–100)
Raw LTV values (e.g. $0–$916) are converted to a 0–100 percentile score using local rank calculation. A contact in the 90th percentile gets a score of 90. This gives Meta a clean, relative value signal regardless of what your actual dollar amounts are.

### 4. Hash all PII
Before any data leaves the app, every piece of personally identifiable information (email, phone, name, location) is SHA-256 hashed. Meta accepts pre-hashed data — your raw customer data never touches Meta's servers.

### 5. Upload to Meta Custom Audience
Contacts are uploaded in batches of 10,000 using Meta's session-based upload API. Each sync **replaces** the audience contents entirely (no accumulation of stale data). The audience ID is reused across syncs — it's pinned in config or remembered from the last successful run.

### 6. Update Lookalike Audience
A 1% US Lookalike Audience is linked to the Custom Audience. When the Custom Audience updates, Meta automatically refreshes the Lookalike. If a Lookalike already exists for this source, it's reused rather than creating a duplicate.

### 7. Record the sync and send email
The sync result (contacts processed, Meta match count, audience IDs, LTV stats) is saved to the database. A success or failure email is sent to your configured address.

---

## Value-based targeting

The key feature is **value-based lookalike audiences**. Instead of just uploading a flat list of customers, each contact gets a value score (0–100). Meta uses this to understand which of your customers are most valuable, then finds new people who resemble your high-value customers.

Contacts with `LTV = 0` are still included — a larger seed audience gives Meta more signal to find matches, and the value weighting means the zero-LTV contacts don't dilute the targeting.

---

## Architecture

| Layer | Tech |
|---|---|
| Backend | Python + FastAPI |
| Frontend | React + Vite + TypeScript |
| Database | PostgreSQL |
| Scheduler | APScheduler (cron `0 2 * * *`) |
| Deployment | Docker Compose |
| Port | `9876` |

The frontend is a React dashboard served as static files by the FastAPI backend. In development, Vite proxies API calls to the backend.

---

## Key configuration

Set once in the UI at `http://localhost:9876`:

| Field | What it is |
|---|---|
| GHL Smart List | Which contact list to sync |
| LTV Custom Field | The GHL field that holds the dollar LTV value |
| Meta Ad Account ID | Your `act_XXXXXXX` ad account |
| Meta Audience ID | Pin an existing audience to reuse (optional) |
| Meta Lookalike ID | Pin an existing lookalike to reuse (optional) |

All API credentials (GHL, Meta, SMTP, Postgres) are set in the `.env` file — see `.env.example`.

---

## Running locally

```bash
# Start everything (Postgres + app)
make up

# View logs
make logs

# Trigger a sync manually (via UI or API)
curl -X POST http://localhost:9876/api/sync/trigger

# Stop
make down
```

---

## Data flow summary

```
GHL Smart List
    → Filter (must have email or phone)
    → Extract LTV (null → 0)
    → Percentile rank (local computation, 0–100)
    → SHA-256 hash all PII
    → Upload to Meta Custom Audience (session replace)
    → Lookalike Audience auto-refreshes
    → Email notification sent
```
