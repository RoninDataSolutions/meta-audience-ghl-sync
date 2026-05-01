# Meta Ad Account Intelligence Audit — Build Spec

This feature integrates into the existing GHL → Meta Audience Sync app. It adds a full ad account audit engine that pulls 7/30/60/90-day performance data from the Meta Marketing API across all campaign types, runs it through multiple AI models for independent analysis, generates a branded PDF intelligence report, and stores everything in Postgres for historical comparison.

---

## Why this exists

Instead of walking a client through Meta Ads Manager and explaining what's working and what's not, you run an audit and hand them a report. Multiple AI models analyze the same data independently — different models catch different things, and sometimes they disagree. That tension is the value.

---

## Architecture (fits into existing stack)

The audit feature plugs into the existing app without changing the sync pipeline:

| Concern | Implementation |
|---|---|
| Backend | New FastAPI router at `/api/audit/*` |
| Data fetch | New `meta_audit.py` service module using the existing `META_ACCESS_TOKEN` from `.env` |
| AI analysis | Direct HTTP calls to Anthropic + OpenAI APIs (no SDK dependencies) |
| Storage | New `audit_reports` Postgres table (same DB as sync history) |
| PDF generation | ReportLab (add to `requirements.txt`) |
| Frontend | New "Audit" tab in the React dashboard |
| Scheduler | Optional APScheduler job (e.g., weekly on Monday at 6 AM) |

No new containers. No new ports. Everything runs inside the existing FastAPI + React + Postgres + Docker Compose stack on port `9876`.

---

## New environment variables

Add to `.env` (alongside existing Meta/GHL/SMTP vars):

```env
# AI Models for audit analysis
ANTHROPIC_API_KEY=sk-ant-xxxxx        # Required — primary analysis
OPENAI_API_KEY=sk-xxxxx               # Optional — second opinion (skipped if blank)

# Audit settings (all optional — sensible defaults)
AUDIT_SCHEDULE_CRON=0 6 * * 1         # Weekly Monday 6 AM (default: disabled)
AUDIT_EMAIL_TO=                        # Override: send audit report to different email than sync notifications
```

The `META_ACCESS_TOKEN` from the existing sync config serves as the **default** token. Individual ad accounts can optionally override it with their own token (see Multi-Account Support below).

---

## Multi-account support

The audit tool works with any number of Meta ad accounts. There are two token strategies depending on how accounts are related:

**Strategy A: Shared token (same Business Manager)**
If all ad accounts live under one Business Manager, a single system user token with `ads_read` on each account is enough. Set this token as `META_ACCESS_TOKEN` in `.env` — it becomes the default. When adding accounts in the UI, leave the token field blank and the default is used.

**Strategy B: Per-account tokens (different Business Managers / different clients)**
If an ad account belongs to a different Business Manager (e.g., a client's own BM), they'll need to grant your system user partner access, OR you store a separate token for that account. The UI has an optional token field per account — if set, it overrides the default.

### Token resolution order

When the audit runs for a given account:
1. Use the account's `meta_access_token` from the `ad_accounts` table if it's not null
2. Fall back to `META_ACCESS_TOKEN` from `.env`
3. If neither exists, fail with a clear error

### Database: `ad_accounts` table

```sql
CREATE TABLE IF NOT EXISTS ad_accounts (
    id              SERIAL PRIMARY KEY,
    account_id      VARCHAR(50) NOT NULL UNIQUE,   -- act_XXXXXXX
    account_name    VARCHAR(255) NOT NULL,          -- Human-readable label (e.g., "PayTechPlus", "Trey's Account")
    
    -- Optional per-account Meta token (encrypted at rest — see note below)
    -- If null, falls back to META_ACCESS_TOKEN from .env
    meta_access_token TEXT,
    
    -- Notification overrides (optional — falls back to global SMTP config)
    notification_email VARCHAR(255),
    
    -- Scheduling (optional — per-account cron override)
    audit_cron      VARCHAR(50),                    -- e.g., "0 6 * * 1" — null means use global or manual only
    
    -- Status
    is_active       BOOLEAN NOT NULL DEFAULT true,
    last_audit_at   TIMESTAMP,
    
    -- Metadata
    currency        VARCHAR(10),                    -- populated on first audit from Meta API
    timezone_name   VARCHAR(100),                   -- populated on first audit from Meta API
    
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ad_accounts_account_id ON ad_accounts(account_id);
```

**Token storage note:** The `meta_access_token` column stores Meta API tokens. For production, encrypt this column at rest using Postgres `pgcrypto` or application-level encryption with a key from `.env` (e.g., `ENCRYPTION_KEY`). For initial build, plaintext in the DB is acceptable — add encryption as a hardening step.

### Backend: `POST /api/accounts` (CRUD endpoints)

New router at `/api/accounts`:

**`POST /api/accounts`** — Add an ad account
```json
{
    "account_id": "act_123456789",
    "account_name": "PayTechPlus",
    "meta_access_token": null,
    "notification_email": "trey@paytechplus.com",
    "audit_cron": "0 6 * * 1"
}
```
On creation, make a test call to `GET /{account_id}?fields=name,currency,timezone_name` using the resolved token. If it succeeds, populate `currency` and `timezone_name` from the response and return success. If it fails (invalid token, no permissions), return an error — don't save a broken account.

**`GET /api/accounts`** — List all accounts
```json
{
    "accounts": [
        {
            "id": 1,
            "account_id": "act_123456789",
            "account_name": "PayTechPlus",
            "has_custom_token": true,
            "notification_email": "trey@paytechplus.com",
            "audit_cron": "0 6 * * 1",
            "is_active": true,
            "last_audit_at": "2026-05-01T06:00:00Z",
            "currency": "USD",
            "timezone_name": "US/Central"
        }
    ]
}
```
Note: never return the actual token value in list/detail responses. Return `has_custom_token: true/false` instead.

**`PUT /api/accounts/{id}`** — Update an account (name, token, email, cron, is_active)

**`DELETE /api/accounts/{id}`** — Soft delete (set `is_active = false`). Don't delete audit_reports — they reference the account_id string, not the ad_accounts row.

**`POST /api/accounts/{id}/test`** — Test the token by calling Meta's account info endpoint. Returns success/failure + account name from Meta.

### Backward compatibility

If no accounts exist in the `ad_accounts` table, the audit trigger still works by accepting `account_id` in the request body (or falling back to `META_AD_ACCOUNT_ID` from `.env`). This means the app works immediately without configuring any accounts — the table is an upgrade path, not a requirement.

### Scheduler changes

Replace the single global cron job with per-account scheduling:

```python
# On app startup, schedule audits for each active account with a cron set
for account in get_active_accounts_with_cron():
    scheduler.add_job(
        run_scheduled_audit,
        CronTrigger.from_crontab(account.audit_cron),
        id=f"meta_audit_{account.account_id}",
        name=f"Audit: {account.account_name}",
        kwargs={"account_id": account.account_id},
    )

# Also keep the global fallback if AUDIT_SCHEDULE_CRON is set and no per-account crons exist
```

When an account is added/updated/deleted via the API, dynamically add/modify/remove the scheduler job without restarting the app.

---

## Database: `audit_reports` table

Add this migration to the existing DB init:

```sql
CREATE TABLE IF NOT EXISTS audit_reports (
    id              SERIAL PRIMARY KEY,
    account_id      VARCHAR(50) NOT NULL,
    generated_at    TIMESTAMP NOT NULL DEFAULT NOW(),

    -- Raw metrics snapshot (the full dataset sent to AI models)
    raw_metrics     JSONB NOT NULL,

    -- AI analyses keyed by model name: {"claude": {...}, "openai": {...}}
    analyses        JSONB NOT NULL DEFAULT '{}',

    -- Summary stats for quick comparison without loading full JSON
    total_spend_7d      DECIMAL(12,2),
    total_spend_30d     DECIMAL(12,2),
    total_conversions_7d  INTEGER,
    total_conversions_30d INTEGER,
    total_impressions_7d  BIGINT,
    total_impressions_30d BIGINT,
    total_clicks_7d     INTEGER,
    total_clicks_30d    INTEGER,
    avg_cpa_30d         DECIMAL(10,2),
    avg_ctr_30d         DECIMAL(6,3),
    avg_roas_30d        DECIMAL(10,2),
    campaign_count      INTEGER,
    audience_count      INTEGER,

    -- PDF binary (stored in DB for simplicity — these are small, ~50-100KB)
    pdf_report      BYTEA,
    pdf_filename    VARCHAR(255),

    -- Status
    status          VARCHAR(20) NOT NULL DEFAULT 'completed',  -- completed | failed | in_progress
    error_message   TEXT,

    -- Model metadata
    models_used     VARCHAR(255),  -- "claude,openai"

    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_reports_account ON audit_reports(account_id);
CREATE INDEX idx_audit_reports_generated ON audit_reports(generated_at DESC);
```

---

## Backend: New files

### File: `backend/services/meta_audit.py`

This is the core audit engine. It has four responsibilities:

---

#### 1. Data fetching

Pull data from the Meta Marketing API at four levels: account info, insights (campaign/ad set/ad), ad creative metadata, audience metadata, and breakdown dimensions.

**Time windows:** 7d, 30d, 60d, 90d.

The 7-day window is critical — it surfaces what's happening *right now*. A campaign can look fine over 30 days but be in freefall this week. The AI models compare 7d vs 30d to detect inflection points.

##### 1a. Account info

```
GET /{account_id}?fields=name,account_id,currency,timezone_name,account_status
```

##### 1b. Insights (campaign, ad set, ad levels)

```
GET /{account_id}/insights?level={level}&time_range={"since":"YYYY-MM-DD","until":"YYYY-MM-DD"}&fields={fields}&limit=500
```

**Insight fields to request (all levels):**
```
campaign_name, campaign_id,
adset_name, adset_id,
ad_name, ad_id,
objective,
spend, impressions, reach, clicks,
cpc, cpm, ctr, frequency,
actions, action_values, cost_per_action_type,
conversions, conversion_values, cost_per_conversion
```

**Time increment strategy:**
- Campaign level: `time_increment=1` (daily rows) for ALL windows. Daily granularity is needed for frequency trending and 7d-vs-30d inflection detection.
- Ad set level: `time_increment=1` for 7d and 30d windows (frequency trending). `time_increment=all_days` for 60d and 90d (reduces row count).
- Ad level: `time_increment=all_days` for all windows (one row per ad per window is enough — creative analysis doesn't need daily granularity).

Filter: `filtering=[{"field":"impressions","operator":"GREATER_THAN","value":"0"}]`

Paginate all requests — follow `paging.next` until exhausted.

##### 1c. Ad creative metadata

After fetching ad-level insights, collect the unique `ad_id` values and fetch creative details:

```
GET /{ad_id}?fields=creative{id,thumbnail_url,body,title,call_to_action_type,object_type,video_id,image_url,link_url}
```

Also fetch the ad's `adcreatives` edge for format info:

```
GET /{ad_id}/adcreatives?fields=object_type,call_to_action_type,title,body,image_url,video_id,thumbnail_url,link_url
```

The `object_type` field tells you the creative format:
- `SHARE` = link ad (single image/video with link)
- `VIDEO` = video ad
- `PHOTO` = image ad
- `CAROUSEL` = carousel
- `STATUS` = text-only

Map each `ad_id` to its creative metadata and include it in the ad summary.

**Rate limiting:** Batch creative fetches. If there are more than 50 unique ads, use batch requests (`POST /?batch=[...]`) with up to 50 items per batch to avoid rate limits. Add a 1-second delay between batches.

##### 1d. Custom audiences

```
GET /{account_id}/customaudiences?fields=name,id,approximate_count_lower_bound,approximate_count_upper_bound,subtype,time_updated,delivery_status
```

##### 1e. Breakdown dimensions (30d window only)

Breakdowns give the real optimization signals — which platforms, placements, demographics are performing. Fetch for the 30d window at the ad set level:

**Platform + placement breakdown:**
```
GET /{account_id}/insights?level=adset&time_range={30d}&fields=adset_name,adset_id,spend,impressions,clicks,ctr,cpc,cpm,actions,action_values&breakdowns=publisher_platform,platform_position&time_increment=all_days
```

This returns rows split by placement combinations like:
- facebook / feed
- facebook / marketplace
- instagram / stream (feed)
- instagram / story
- instagram / reels
- audience_network / classic
- messenger / messenger_home

**Age + gender breakdown:**
```
GET /{account_id}/insights?level=adset&time_range={30d}&fields=adset_name,adset_id,spend,impressions,clicks,actions,action_values&breakdowns=age,gender&time_increment=all_days
```

Age buckets: 18-24, 25-34, 35-44, 45-54, 55-64, 65+
Gender: male, female, unknown

Only fetch breakdowns for the 30d window to keep token count manageable. The AI models get enough signal from 30 days of segmented data.

---

#### 2. Data summarization

Raw API rows need to be collapsed into structured summaries. This section is critical — the quality of the audit depends on giving the AI models clean, complete data.

##### 2a. Universal action extraction

The audit must work for ANY campaign objective — lead gen, e-commerce, traffic, awareness, engagement, app installs, video views, messaging. Extract ALL action types from the `actions`, `action_values`, and `cost_per_action_type` arrays.

For each row, build a complete actions map:

```python
def extract_all_actions(row: dict) -> dict:
    """
    Returns:
    {
        "lead": {"count": 12, "value": 600.0, "cost": 50.0},
        "purchase": {"count": 3, "value": 1200.0, "cost": 133.33},
        "link_click": {"count": 450, "value": 0, "cost": 0.22},
        "landing_page_view": {"count": 180, "value": 0, "cost": 0.56},
        "video_view": {"count": 5000, "value": 0, "cost": 0.02},
        "post_engagement": {"count": 800, "value": 0, "cost": 0.13},
        ...
    }
    """
    actions_map = {}

    for a in row.get("actions") or []:
        atype = a.get("action_type", "")
        if atype not in actions_map:
            actions_map[atype] = {"count": 0, "value": 0.0, "cost": 0.0}
        actions_map[atype]["count"] += int(a.get("value", 0))

    for a in row.get("action_values") or []:
        atype = a.get("action_type", "")
        if atype in actions_map:
            actions_map[atype]["value"] += float(a.get("value", 0))

    for a in row.get("cost_per_action_type") or []:
        atype = a.get("action_type", "")
        if atype in actions_map:
            actions_map[atype]["cost"] = float(a.get("value", 0))

    return actions_map
```

Then determine the **primary conversion action** for each campaign based on its `objective` field:

```python
OBJECTIVE_TO_PRIMARY_ACTION = {
    "OUTCOME_LEADS": "lead",
    "LEAD_GENERATION": "lead",
    "OUTCOME_SALES": "purchase",
    "CONVERSIONS": "offsite_conversion",
    "OUTCOME_TRAFFIC": "link_click",
    "LINK_CLICKS": "link_click",
    "OUTCOME_ENGAGEMENT": "post_engagement",
    "POST_ENGAGEMENT": "post_engagement",
    "OUTCOME_AWARENESS": "impressions",
    "BRAND_AWARENESS": "impressions",
    "REACH": "impressions",
    "OUTCOME_APP_PROMOTION": "app_install",
    "APP_INSTALLS": "app_install",
    "VIDEO_VIEWS": "video_view",
    "MESSAGES": "onsite_conversion.messaging_conversation_started_7d",
    "STORE_VISITS": "store_visit",
}
```

If the objective isn't in this map or the primary action type has zero results, fall back to the action type with the highest `count` (excluding generic ones like `page_engagement`, `post`, `comment`, `like`).

Include both the `primary_action` summary AND the full `all_actions` map in each entity's summary so the AI models can see the complete picture.

##### 2b. Campaign summarization (from daily rows → per-campaign totals)

For each unique `campaign_id`, aggregate across daily rows:

- **Sum:** spend, impressions, reach, clicks, days_active (count of daily rows)
- **Sum per action type:** aggregate all action counts, values, costs across days
- **Derive:**
  - CPM = spend / impressions × 1000
  - CPC = spend / clicks
  - CTR = clicks / impressions × 100
  - CPA = spend / primary_action_count (cost per primary conversion)
  - ROAS = primary_action_value / spend (return on ad spend — only meaningful if value tracking is set up)
  - frequency = impressions / reach
  - frequency_7d = (sum of last 7 daily impression rows) / (sum of last 7 daily reach rows) — **trailing 7-day frequency**
  - frequency_trend = frequency_7d - frequency (positive = frequency is accelerating = fatigue risk)

Include `objective` from the first row for this campaign.

**Output per campaign:**
```json
{
    "campaign_id": "...",
    "campaign_name": "...",
    "objective": "OUTCOME_LEADS",
    "spend": 2450.00,
    "impressions": 185000,
    "reach": 142000,
    "clicks": 3700,
    "ctr": 2.0,
    "cpc": 0.66,
    "cpm": 13.24,
    "frequency": 1.30,
    "frequency_7d": 1.85,
    "frequency_trend": 0.55,
    "days_active": 30,
    "primary_action": "lead",
    "primary_action_count": 100,
    "primary_action_value": 7840.00,
    "primary_action_cost": 24.50,
    "roas": 3.2,
    "all_actions": {
        "lead": {"count": 100, "value": 7840.0, "cost": 24.50},
        "link_click": {"count": 3200, "value": 0, "cost": 0.77},
        "landing_page_view": {"count": 2100, "value": 0, "cost": 1.17},
        "page_engagement": {"count": 4500, "value": 0, "cost": 0.54}
    }
}
```

##### 2c. Ad set summarization

Same aggregation pattern as campaigns. For 7d and 30d windows (which have daily rows), compute frequency_7d and frequency_trend. For 60d/90d (which have `all_days` rows), frequency_7d is null.

Include parent `campaign_name` and `campaign_id` for context.

**Additional ad set fields:**
```json
{
    "adset_id": "...",
    "adset_name": "...",
    "campaign_name": "...",
    "campaign_id": "...",
    "objective": "...",
    "spend": 890.00,
    "impressions": 62000,
    "reach": 28000,
    "clicks": 1860,
    "ctr": 3.0,
    "cpc": 0.48,
    "cpm": 14.35,
    "frequency": 2.21,
    "frequency_7d": 2.95,
    "frequency_trend": 0.74,
    "primary_action": "lead",
    "primary_action_count": 20,
    "primary_action_cost": 44.50,
    "primary_action_value": 1602.00,
    "roas": 1.80,
    "all_actions": { ... }
}
```

##### 2d. Ad summarization (with creative metadata)

Ad-level uses `all_days` rows, so no daily frequency computation — just the aggregate frequency from impressions/reach.

After summarizing performance metrics, merge in the creative metadata fetched in step 1c:

```json
{
    "ad_id": "...",
    "ad_name": "...",
    "adset_name": "...",
    "adset_id": "...",
    "campaign_name": "...",
    "campaign_id": "...",
    "spend": 445.00,
    "impressions": 31000,
    "reach": 22000,
    "clicks": 930,
    "ctr": 3.0,
    "cpc": 0.48,
    "frequency": 1.41,
    "primary_action": "lead",
    "primary_action_count": 10,
    "primary_action_cost": 44.50,
    "roas": 1.80,
    "all_actions": { ... },

    "creative": {
        "format": "VIDEO",
        "headline": "Stop Losing Customers to Bad Payments",
        "body": "PayTechPlus helps merchants...",
        "call_to_action": "LEARN_MORE",
        "thumbnail_url": "https://...",
        "video_id": "120001",
        "link_url": "https://paytechplus.com/demo"
    }
}
```

If creative metadata fetch fails for an ad (permissions, deleted creative, etc.), set `"creative": null` — don't let it block the report.

##### 2e. Breakdown summarization (30d only)

**Platform + placement breakdown** → group by `publisher_platform` + `platform_position`:

```json
{
    "breakdowns_by_placement": [
        {
            "platform": "instagram",
            "position": "stream",
            "spend": 1200.00,
            "impressions": 95000,
            "clicks": 2800,
            "ctr": 2.95,
            "cpc": 0.43,
            "cpm": 12.63,
            "primary_action_count": 55,
            "primary_action_cost": 21.82,
            "pct_of_total_spend": 35.9
        },
        {
            "platform": "facebook",
            "position": "feed",
            "spend": 980.00,
            ...
        }
    ]
}
```

Calculate `pct_of_total_spend` for each row as a share of total 30d spend.

**Age + gender breakdown** → group by `age` + `gender`:

```json
{
    "breakdowns_by_demographic": [
        {
            "age": "25-34",
            "gender": "male",
            "spend": 800.00,
            "impressions": 62000,
            "clicks": 1500,
            "primary_action_count": 35,
            "primary_action_cost": 22.86,
            "pct_of_total_spend": 23.9
        }
    ]
}
```

Sort both breakdown lists by spend descending.

##### 2f. Final payload structure

```json
{
    "generated_at": "2026-05-01T14:00:00Z",
    "account": {
        "name": "PayTechPlus",
        "account_id": "act_123456789",
        "currency": "USD",
        "timezone_name": "US/Central",
        "account_status": 1
    },
    "windows": {
        "7d": {
            "campaigns": [ ... ],
            "adsets": [ ... ],
            "ads": [ ... ]
        },
        "30d": {
            "campaigns": [ ... ],
            "adsets": [ ... ],
            "ads": [ ... ]
        },
        "60d": {
            "campaigns": [ ... ],
            "adsets": [ ... ],
            "ads": [ ... ]
        },
        "90d": {
            "campaigns": [ ... ],
            "adsets": [ ... ],
            "ads": [ ... ]
        }
    },
    "breakdowns_30d": {
        "by_placement": [ ... ],
        "by_demographic": [ ... ]
    },
    "audiences": [ ... ]
}
```

If the serialized payload exceeds 120,000 characters, apply this truncation priority:
1. Remove 90d ad-level data (keep campaigns + adsets)
2. Remove 60d ad-level data
3. Remove 90d adset-level data
4. Truncate `all_actions` to top 5 action types by count per entity
5. Remove `creative.body` and `creative.headline` (keep format + CTA)

Add a note at the end of the payload: `"_truncation_note": "Payload was truncated to fit context limits. Removed: [list what was cut]"`

---

#### 3. Multi-model AI analysis

Send the summarized payload to each configured model with this system prompt:

```
You are a senior paid-media strategist auditing a Meta (Facebook/Instagram) ad account. This account may run any combination of campaign objectives — lead gen, e-commerce/purchase, traffic, awareness, engagement, app installs, video views, or messaging. Analyze whatever is present.

You will receive structured performance data for 7-day, 30-day, 60-day, and 90-day windows at campaign, ad set, and ad levels, plus platform/placement breakdowns, demographic breakdowns, creative metadata, and audience information.

Key fields to understand:
- "objective": the Meta campaign objective (OUTCOME_LEADS, OUTCOME_SALES, OUTCOME_TRAFFIC, etc.)
- "primary_action": the conversion event this campaign optimizes for (lead, purchase, link_click, etc.)
- "primary_action_count/cost/value": metrics for that primary conversion
- "all_actions": complete map of every action type and its count/value/cost
- "roas": primary_action_value / spend (only meaningful if value tracking exists — if 0, value tracking is not set up, not that ROAS is actually zero)
- "frequency_7d": trailing 7-day frequency (if available)
- "frequency_trend": frequency_7d minus overall frequency — positive means frequency is accelerating (fatigue risk)
- "creative.format": VIDEO, PHOTO, CAROUSEL, SHARE, STATUS
- "breakdowns_30d.by_placement": performance split by platform (facebook/instagram) and position (feed/stories/reels/etc.)
- "breakdowns_30d.by_demographic": performance split by age bracket and gender

Produce a thorough intelligence report in the following JSON structure (no markdown, no backticks — raw JSON only):

{
    "executive_summary": "2-3 paragraph overview of account health, covering all active campaign types",

    "campaign_by_campaign": [
        {
            "campaign_name": "...",
            "objective": "...",
            "verdict": "strong | decent | underperforming | critical",
            "summary": "2-3 sentence assessment",
            "key_metrics": "cite the numbers that matter for this objective",
            "recommendation": "specific next step"
        }
    ],

    "whats_working": [
        {"finding": "...", "evidence": "...", "recommendation": "..."}
    ],

    "whats_not_working": [
        {"finding": "...", "evidence": "...", "recommendation": "..."}
    ],

    "opportunities": [
        {"opportunity": "...", "rationale": "...", "expected_impact": "..."}
    ],

    "creative_analysis": {
        "summary": "Overall creative health — format mix, messaging patterns, fatigue signals",
        "by_format": [
            {
                "format": "VIDEO | PHOTO | CAROUSEL | ...",
                "ad_count": 5,
                "total_spend": 1200,
                "avg_ctr": 2.1,
                "avg_cpa": 22.50,
                "assessment": "How this format is performing relative to others"
            }
        ],
        "fatigue_signals": [
            {"ad_or_adset": "...", "signal": "...", "action": "..."}
        ],
        "recommendations": ["..."]
    },

    "placement_analysis": {
        "summary": "Which platforms and placements are delivering, which are wasting budget",
        "top_performers": [
            {"platform": "...", "position": "...", "why": "...", "metrics": "..."}
        ],
        "underperformers": [
            {"platform": "...", "position": "...", "why": "...", "metrics": "...", "action": "..."}
        ],
        "recommendations": ["..."]
    },

    "demographic_analysis": {
        "summary": "Which age/gender segments convert, which don't",
        "top_segments": [
            {"segment": "Males 25-34", "metrics": "...", "insight": "..."}
        ],
        "wasted_spend_segments": [
            {"segment": "...", "spend": "...", "conversions": "...", "action": "..."}
        ],
        "recommendations": ["..."]
    },

    "audience_analysis": "Paragraph on custom audience health — sizes, types, seed quality, match rates if inferable",

    "budget_allocation": {
        "summary": "Overall spend efficiency and reallocation suggestions",
        "current_split": "How budget is distributed across campaigns/objectives",
        "recommended_changes": ["..."],
        "estimated_impact": "What reallocation could achieve"
    },

    "trend_analysis": {
        "seven_vs_thirty": "Compare 7d to 30d — is performance improving, declining, or stable this week? Call out any inflection points.",
        "thirty_vs_sixty_vs_ninety": "Longer-term trajectory. Seasonal patterns, scaling effects, diminishing returns.",
        "frequency_trends": "Which campaigns/adsets show accelerating frequency? How close are they to fatigue thresholds?"
    },

    "risk_flags": ["..."],

    "priority_actions": ["Top 5 ordered actions to take this week, with expected impact for each"]
}

Be specific — cite campaign names, ad set names, ad names, creative formats, placement names, demographic segments, and actual numbers throughout. Don't hedge. Give clear, actionable direction. If data is thin for any section, say so explicitly and explain what it means for the analysis.
```

**Claude (Anthropic) — required:**
```
POST https://api.anthropic.com/v1/messages
Headers: x-api-key, anthropic-version: 2023-06-01, content-type: application/json
Body: { model: "claude-sonnet-4-20250514", max_tokens: 8192, system: <system_prompt>, messages: [{ role: "user", content: "Here is the full audit data:\n\n<payload_json>" }] }
Response: data.content[0].text → strip markdown fences → JSON.parse
```

**OpenAI (GPT-4o) — optional, skip if no API key:**
```
POST https://api.openai.com/v1/chat/completions
Headers: Authorization: Bearer <key>, Content-Type: application/json
Body: { model: "gpt-4o", max_tokens: 8192, messages: [{ role: "system", content: <system_prompt> }, { role: "user", content: "Here is the full audit data:\n\n<payload_json>" }] }
Response: data.choices[0].message.content → strip markdown fences → JSON.parse
```

Note: `max_tokens` increased to 8192 (from 4096) because the expanded analysis structure with per-campaign verdicts, creative analysis, placement analysis, and demographic analysis needs more room.

Both models use 120-second timeout. Wrap in try/except — if a model fails, store `{"error": "<message>"}` for that model and continue. A model failure should not block the report.

To strip markdown fences from response text before JSON parsing:
```python
text = text.strip()
if text.startswith("```"):
    text = text.split("\n", 1)[1]
if text.endswith("```"):
    text = text.rsplit("```", 1)[0]
text = text.strip()
```

---

#### 4. Historical comparison

When generating a report, query the most recent previous report for the same account:

```sql
SELECT raw_metrics, analyses, generated_at,
       total_spend_7d, total_spend_30d,
       total_conversions_7d, total_conversions_30d,
       total_impressions_7d, total_impressions_30d,
       total_clicks_7d, total_clicks_30d,
       avg_cpa_30d, avg_ctr_30d, avg_roas_30d
FROM audit_reports
WHERE account_id = $1 AND status = 'completed'
ORDER BY generated_at DESC
LIMIT 1
```

Compute deltas for all summary stats:
- Spend (7d and 30d): current vs previous (absolute + percentage)
- Conversions (7d and 30d): current vs previous
- Impressions (7d and 30d): current vs previous
- CPA: current vs previous
- CTR: current vs previous
- ROAS: current vs previous

Include these deltas in the PDF report's comparison section and in the API response.

---

### File: `backend/services/audit_pdf.py`

Generates the branded PDF using ReportLab. Add `reportlab` to `requirements.txt`.

**Report structure (page by page):**

**Page 1 — Cover + Account Snapshot**
- Title: "META AD ACCOUNT INTELLIGENCE AUDIT" (Helvetica-Bold, 22pt, dark navy #1a1a2e)
- Subtitle: "{Account Name} • Generated {date}" (11pt, gray #6c757d)
- Horizontal rule (2px, accent red #e94560)
- Account snapshot table (3 columns: Metric | Last 7 Days | Last 30 Days):
  - Total Spend
  - Impressions
  - Clicks
  - CTR
  - Conversions (primary action)
  - Avg CPA
  - ROAS (if value tracking exists, otherwise show "—")
  - Active Campaigns
  - Active Ad Sets
  - Active Ads
- Table styling: dark header row (#1a1a2e with white text), light gray body (#f8f9fa), 0.5px grid (#dee2e6)
- If comparison data exists, add a 4th column "Δ vs Previous" with directional arrows and percentages

**Page 2 — Campaign Performance Table (30 days)**
- Section header: "CAMPAIGN PERFORMANCE — LAST 30 DAYS" (15pt, accent #e94560)
- Table columns: Campaign | Objective | Spend | Impr | Clicks | CTR | Conv | CPA | ROAS | Freq | Freq 7d
- Sorted by spend descending
- Alternating row backgrounds (white / #f8f9fa)
- Campaign names truncated to 35 chars in a Paragraph element (allows wrapping)
- "Conv" = primary_action_count, "CPA" = primary_action_cost
- "ROAS" shows "—" if no value tracking
- "Freq 7d" column highlights in amber if frequency_7d > 3.0, red if > 5.0
- Font size 7.5pt for table body (11 columns needs tight sizing)

**Page 3 — Ad Set Performance Table (30 days, top 20 by spend)**
- Section header: "TOP AD SETS — LAST 30 DAYS"
- Table columns: Ad Set | Campaign | Spend | Clicks | CTR | Conv | CPA | Freq | Freq 7d
- Only show top 20 ad sets by spend to keep the table readable
- Same frequency highlighting rules

**Page 4 — Ad Creative Performance Table (30 days, top 20 by spend)**
- Section header: "TOP ADS — LAST 30 DAYS"
- Table columns: Ad Name | Format | Spend | Impr | CTR | Conv | CPA | Freq
- "Format" = creative.format (VIDEO, PHOTO, CAROUSEL, etc.) or "—" if creative metadata unavailable
- Top 20 by spend

**Page 5 — Placement Breakdown (30 days)**
- Section header: "PERFORMANCE BY PLACEMENT"
- Table columns: Platform | Position | Spend | % of Spend | Impr | Clicks | CTR | Conv | CPA
- Sorted by spend descending
- Highlight the best CPA row in green, worst in red

**Page 6 — Demographic Breakdown (30 days)**
- Section header: "PERFORMANCE BY DEMOGRAPHIC"
- Table columns: Age | Gender | Spend | % of Spend | Clicks | Conv | CPA
- Sorted by spend descending
- Top 15 segments

**Pages 7+ — AI Analysis Sections (one per model)**

Each model gets a color-coded badge and full analysis block:
- Model badge: colored background strip (Claude = #d97706 amber, OpenAI = #10a37f green) with white bold text "{MODEL} ANALYSIS"

Rendered in order:

- **Executive Summary** — paragraph

- **Campaign-by-Campaign Verdicts** — table with columns: Campaign | Objective | Verdict | Recommendation
  - Verdict cell color-coded: strong = green bg, decent = light blue bg, underperforming = amber bg, critical = red bg

- **What's Working** — each item: bold finding, italic "Evidence:" + text, italic "Action:" + text, with 4pt spacer

- **What's Not Working** — same format

- **Opportunities** — each item: bold opportunity, italic "Rationale:" + text, italic "Expected Impact:" + text

- **Creative Analysis** — sub-header "Creative Health", then the summary paragraph. Sub-header "Performance by Format" — small table (Format | Ads | Spend | Avg CTR | Avg CPA | Assessment). Then "Fatigue Signals" as warning-styled items. Then "Creative Recommendations" as numbered list.

- **Placement Analysis** — sub-header "Placement Intelligence". Summary paragraph. "Top Performers" and "Underperformers" as card-style items. Recommendations as numbered list.

- **Demographic Analysis** — sub-header "Demographic Intelligence". Summary paragraph. "Top Segments" and "Wasted Spend" as items. Recommendations.

- **Audience Analysis** — paragraph

- **Budget Allocation** — summary paragraph, then "Current Split" paragraph, then "Recommended Changes" numbered list, then "Estimated Impact" paragraph.

- **Trend Analysis** — three sub-sections:
  - "This Week vs Last 30 Days (7d vs 30d)" — paragraph from `seven_vs_thirty`
  - "Long-Term Trajectory (30d/60d/90d)" — paragraph from `thirty_vs_sixty_vs_ninety`
  - "Frequency Trends" — paragraph from `frequency_trends`

- **Risk Flags** — each prefixed with "⚠"

- **Priority Actions This Week** — numbered list, each with expected impact

Page break between models.

**Final page — Historical Comparison (if previous report exists)**
- Section header: "COMPARISON TO PREVIOUS REPORT"
- Previous report date
- Delta table (3 columns: Metric | Previous → Current | Change):
  - Spend (7d)
  - Spend (30d)
  - Conversions (7d)
  - Conversions (30d)
  - CPA (30d)
  - CTR (30d)
  - ROAS (30d)
  - Impressions (30d)
- Color-coded: green for improvements, red for declines (direction-aware — lower CPA is green, lower conversions is red)

**Footer (on every page)**
Use `doc.build` with an `onPage`/`onLaterPages` callback to draw on every page:
- Thin gray HR near bottom margin
- Left-aligned: "Generated by Ronin Data Solutions" (7pt, gray)
- Right-aligned: "Page X of Y" (7pt, gray) — use `canvas.getPageNumber()` and build in two passes or use `PageTemplate` with `onPage` callback
- Center: "Confidential" (7pt, gray)

**ReportLab settings:**
- Page size: letter
- Margins: 0.75in left/right, 0.6in top/bottom
- Use `BaseDocTemplate` with a custom `PageTemplate` and `onPage` callback (not `SimpleDocTemplate`) so you can draw the footer on every page
- Flowables: `Paragraph`, `Table`, `Spacer`, `HRFlowable`, `PageBreak`

---

### File: `backend/routers/audit.py`

New FastAPI router. Mount at `/api/audit` in the main app.

#### Endpoints

**`POST /api/audit/trigger`**

Triggers a new audit. Runs in background (returns immediately with a job ID).

Request body (all optional — uses config/env defaults):
```json
{
    "account_id": "act_XXXXX",
    "models": ["claude", "openai"],
    "include_comparison": true
}
```

Account resolution order:
1. `account_id` from request body — if provided, look it up in `ad_accounts` table to get the token + settings
2. If not in request body, fall back to `META_AD_ACCOUNT_ID` from `.env`
3. Token resolved per the token resolution order (per-account token → default `.env` token)

Response:
```json
{
    "status": "started",
    "report_id": 42,
    "account_id": "act_XXXXX",
    "account_name": "PayTechPlus",
    "message": "Audit started. Poll /api/audit/reports/42 for status."
}
```

Implementation: create an `audit_reports` row with `status='in_progress'`, then kick off the audit in a background task (`BackgroundTasks` or run in a thread). The background task:
1. Fetches Meta data — account info, insights at all levels for 7d/30d/60d/90d, ad creative metadata, audiences, breakdowns
2. Summarizes all data into the structured payload
3. Runs AI analyses (Claude, optionally OpenAI)
4. Loads previous report for comparison
5. Generates PDF
6. Updates the DB row with results, PDF bytes, summary stats, and `status='completed'`
7. Sends email notification if configured (reuse existing SMTP logic from sync)
8. On any exception: update row with `status='failed'` and `error_message`

**`GET /api/audit/reports`**

List all audit reports (paginated, most recent first). Optionally filter by account.

Query params: `limit` (default 10), `offset` (default 0), `account_id` (optional — filter to one account)

Response:
```json
{
    "reports": [
        {
            "id": 42,
            "account_id": "act_XXXXX",
            "account_name": "PayTechPlus",
            "generated_at": "2026-05-01T14:00:00Z",
            "status": "completed",
            "total_spend_7d": 850.00,
            "total_spend_30d": 3340.00,
            "total_conversions_30d": 120,
            "avg_cpa_30d": 27.83,
            "avg_roas_30d": 3.2,
            "campaign_count": 4,
            "models_used": "claude,openai",
            "has_pdf": true
        }
    ],
    "total": 5
}
```

To resolve `account_name`: left join `audit_reports.account_id` against `ad_accounts.account_id`. If no match in `ad_accounts` (legacy reports or env-only accounts), use the `account_id` string as the name.

**`GET /api/audit/reports/{report_id}`**

Full report detail including all analyses, raw metrics, and comparison data.

Response:
```json
{
    "id": 42,
    "account_id": "act_XXXXX",
    "generated_at": "2026-05-01T14:00:00Z",
    "status": "completed",
    "raw_metrics": { ... },
    "analyses": {
        "claude": { ... },
        "openai": { ... }
    },
    "summary": {
        "total_spend_7d": 850.00,
        "total_spend_30d": 3340.00,
        "total_conversions_7d": 32,
        "total_conversions_30d": 120,
        "avg_cpa_30d": 27.83,
        "avg_ctr_30d": 2.15,
        "avg_roas_30d": 3.2,
        "campaign_count": 4
    },
    "comparison": {
        "previous_report_id": 41,
        "previous_generated_at": "2026-04-01T14:00:00Z",
        "deltas": {
            "spend_7d": { "previous": 780, "current": 850, "change_pct": 8.97 },
            "spend_30d": { "previous": 2800, "current": 3340, "change_pct": 19.3 },
            "conversions_30d": { "previous": 95, "current": 120, "change_pct": 26.3 },
            "cpa_30d": { "previous": 29.47, "current": 27.83, "change_pct": -5.6 },
            "roas_30d": { "previous": 2.8, "current": 3.2, "change_pct": 14.3 },
            "impressions_30d": { "previous": 200000, "current": 247000, "change_pct": 23.5 }
        }
    },
    "models_used": "claude,openai"
}
```

**`GET /api/audit/reports/{report_id}/pdf`**

Download the PDF file.

Response: `StreamingResponse` with `content-type: application/pdf` and `Content-Disposition: attachment; filename="audit_{account}_{date}.pdf"`. Read the `pdf_report` BYTEA from the DB row.

**`GET /api/audit/reports/{report_id}/json`**

Download the raw JSON archive (raw_metrics + analyses).

Response: JSON file download with `Content-Disposition: attachment`.

**`DELETE /api/audit/reports/{report_id}`**

Delete a report.

---

## Frontend: Audit tab

Add a new tab/route to the existing React dashboard. The sync dashboard currently lives at `/` — add an "Audit" tab that routes to `/audit`, and an "Accounts" tab that routes to `/accounts`.

### Accounts page (`/accounts`)

Simple CRUD interface for managing ad accounts:

**Account list table**
- Columns: Name | Account ID | Token Status | Notification Email | Schedule | Last Audit | Status | Actions
- "Token Status" shows "Custom" (green badge) or "Default" (gray badge) — never show the actual token
- "Schedule" shows the cron in human-readable form (e.g., "Mon 6:00 AM") or "Manual only"
- Actions: "Edit" button, "Test Token" button (calls `POST /api/accounts/{id}/test`, shows success/failure flash), "Deactivate" toggle

**Add account form (modal or inline)**
- Account ID: text input, required (validates `act_` prefix)
- Display Name: text input, required
- Meta Access Token: password input, optional — placeholder text: "Leave blank to use default token from .env"
- Notification Email: text input, optional — placeholder: "Falls back to global notification email"
- Audit Schedule: dropdown with presets:
  - Manual only (default)
  - Daily at 6 AM
  - Weekly Monday 6 AM
  - Weekly Friday 6 AM
  - Custom (shows cron input)
- "Test & Save" button — calls the test endpoint first, only saves if the token works

**Edit account form** — same as add, but token field shows "••••••••" if a custom token exists, with a "Change token" link to reveal the input. Submitting with an empty token field does NOT clear the existing token.

### Audit page layout

**Top section: trigger bar**
- **Account selector** — dropdown of all active accounts from `GET /api/accounts`. If only one account exists, auto-select it and hide the dropdown. If no accounts exist, show the `META_AD_ACCOUNT_ID` from a config endpoint as the default.
- "Run Audit" button (primary, accent color)
- Model selector: checkboxes for Claude / OpenAI (checked by default if API key is configured)
- "Include comparison" toggle (default on)
- When audit is in progress: show a progress indicator with status text ("Fetching Meta data...", "Running Claude analysis...", etc.) — poll `GET /api/audit/reports/{id}` every 3 seconds until status != 'in_progress'

**Middle section: report list**
- If multiple accounts exist, show an account filter dropdown above the table (default: "All accounts")
- Table of past reports: Account | Date | Spend (7d) | Spend (30d) | Conv (30d) | CPA | ROAS | Campaigns | Models | Status | Actions
- "Account" column shows the account_name with the account_id as subtitle text
- Actions column: "View" button (opens detail), "PDF" button (downloads PDF), "JSON" button (downloads archive)
- Sort by date descending
- Show deltas from previous report inline (green/red arrows with percentages)

**Bottom section: report detail (when a report is selected)**

This is the main intelligence display.

### Account snapshot bar (always visible when report loaded)

Horizontal metric cards across the top:
- Spend (7d) | Spend (30d) | Conversions (30d) | CPA | ROAS | CTR | Campaigns | Ad Sets | Ads
- Each card shows the value and, if comparison exists, a delta badge (↑12% green or ↓5% red)

### Data tables section (collapsible)

Five collapsible sections showing the raw performance data:
1. **Campaigns** — same columns as PDF campaign table, sortable
2. **Ad Sets** — sortable, filterable by campaign
3. **Ads** — sortable, filterable by campaign/adset, shows creative format badge
4. **Placements** — placement breakdown table
5. **Demographics** — demographic breakdown table

### AI analysis section (tabbed by model)

**Tab per model (e.g., "Claude Analysis" | "OpenAI Analysis")**

Each tab renders:

- **Executive Summary** — card with paragraph text

- **Campaign Verdicts** — card grid, one card per campaign. Each card shows:
  - Campaign name + objective badge
  - Verdict badge (strong/decent/underperforming/critical with corresponding color)
  - Summary text
  - Key metrics
  - Recommendation

- **What's Working** — green-left-bordered cards, each with finding (bold), evidence, recommendation

- **What's Not Working** — red-left-bordered cards, same structure

- **Opportunities** — blue-left-bordered cards with opportunity, rationale, expected impact

- **Creative Analysis** — card with:
  - Summary paragraph
  - Small table: format performance comparison
  - Fatigue signal warnings (amber cards)
  - Recommendations list

- **Placement Analysis** — card with:
  - Summary paragraph
  - Top performers (green badges)
  - Underperformers (red badges with suggested action)
  - Recommendations

- **Demographic Analysis** — card with:
  - Summary paragraph
  - Top segments
  - Wasted spend segments (with suggested exclusions)
  - Recommendations

- **Audience Analysis** — card with paragraph

- **Budget Allocation** — card with summary, current split, recommended changes, estimated impact

- **Trend Analysis** — card with three sub-sections:
  - "This Week vs 30d" with 7d-vs-30d comparison
  - "Long-Term" with 30/60/90d trajectory
  - "Frequency Trends" with fatigue warnings

- **Risk Flags** — amber banner list

- **Priority Actions** — numbered list with expected impact per action, styled as a checklist

**"Compare" tab (if comparison data exists)**
- Side-by-side metric deltas with directional arrows
- All summary stats: Spend (7d), Spend (30d), Conversions (7d/30d), CPA, CTR, ROAS, Impressions
- Color-coded: green for improvements, red for declines (direction-aware — lower CPA is green, lower conversions is red)

---

## Integration points with existing app

### Main app (`main.py` or equivalent)

```python
from routers.audit import router as audit_router
from routers.accounts import router as accounts_router

app.include_router(audit_router, prefix="/api/audit", tags=["audit"])
app.include_router(accounts_router, prefix="/api/accounts", tags=["accounts"])
```

### Scheduler (per-account + global fallback)

Replace the single global cron with per-account scheduling at startup:

```python
# Schedule per-account audits
for account in get_active_accounts_with_cron():
    scheduler.add_job(
        run_scheduled_audit,
        CronTrigger.from_crontab(account.audit_cron),
        id=f"meta_audit_{account.account_id}",
        name=f"Audit: {account.account_name}",
        kwargs={"account_id": account.account_id},
    )

# Global fallback (only if AUDIT_SCHEDULE_CRON is set and no per-account crons cover META_AD_ACCOUNT_ID)
if os.getenv("AUDIT_SCHEDULE_CRON"):
    scheduler.add_job(
        run_scheduled_audit,
        CronTrigger.from_crontab(os.getenv("AUDIT_SCHEDULE_CRON")),
        id="meta_audit_default",
        name="Default Meta Audit",
    )
```

When accounts are added/updated/deleted via the API, dynamically add/modify/remove scheduler jobs without restarting the app using `scheduler.add_job(..., replace_existing=True)` and `scheduler.remove_job(job_id)`.

### Docker / requirements

Add to `requirements.txt`:
```
reportlab>=4.0
```

No new containers, no new ports, no new volumes needed.

### Email notification

Reuse the existing SMTP helper from the sync pipeline. After audit completes, send an email with:
- Subject: "Meta Audit Report — {Account Name} — {Date}"
- Body: summary stats table (7d + 30d spend, conversions, CPA, ROAS) + top 3 priority actions from the primary model (Claude) + deltas from previous report if available
- Attachment: the PDF report

Use `AUDIT_EMAIL_TO` if set, otherwise fall back to the existing `REPORT_EMAIL_TO` or `SMTP_USER`.

---

## Adding more AI models

The architecture is model-agnostic. To add a third model (e.g., Gemini, Llama via Groq, Mistral):

1. Add the API key to `.env` (e.g., `GOOGLE_API_KEY`)
2. Write an `analyse_with_<model>(payload: dict) -> dict` function following the same pattern:
   - POST the shared system prompt + payload to the model's API
   - Parse the response into the standard JSON structure
   - Return the parsed dict (or `{"error": "..."}` on failure)
3. Register it in the model registry so the trigger endpoint and frontend checkbox pick it up
4. Add a badge color for the PDF renderer

The system prompt is shared across all models so they produce identical JSON structure. This makes outputs directly comparable in both the PDF and the frontend tabs.

---

## Meta API rate limiting and error handling

The audit makes a significant number of API calls. Handle rate limits gracefully:

- **Rate limit response (HTTP 429 or error code 17):** Wait for the time specified in the `Retry-After` header (or default 60 seconds), then retry. Max 3 retries per request.
- **Batch creative fetches:** Use the Batch API (`POST /?batch=[...]`) with up to 50 items per batch. 1-second delay between batches.
- **Parallel where safe:** Account info, audience list, and breakdown fetches can run concurrently (they hit different endpoints). Campaign/adset/ad insight fetches for different time windows can also run concurrently. Use `asyncio.gather` or `concurrent.futures.ThreadPoolExecutor`.
- **Timeout:** 60 seconds per individual Meta API request. 10-minute overall timeout for the entire data fetch phase.
- **Token expiration:** If a Meta API call returns error code 190 (expired token), log the error, set the report status to `failed` with a message indicating the token needs refresh, and send a failure notification email.

---

## Data flow summary

```
User selects account from dropdown + clicks "Run Audit" (or per-account cron fires)
    → POST /api/audit/trigger { account_id: "act_XXXXX" }
    → Resolve token: ad_accounts.meta_access_token → .env META_ACCESS_TOKEN
    → Create audit_reports row (status: in_progress)
    → Background task starts:
        → Meta Graph API: fetch account info
        → Meta Graph API: fetch 7d/30d/60d/90d insights (campaign, adset, ad levels)
        → Meta Graph API: fetch ad creative metadata (batch, up to 50/batch)
        → Meta Graph API: fetch 30d breakdowns (placement + demographic)
        → Meta Graph API: fetch custom audiences
        → Summarize: collapse daily rows, extract all action types, compute derived metrics
        → Summarize: merge creative metadata into ad summaries
        → Summarize: compute placement + demographic breakdowns with spend percentages
        → Detect primary action per campaign from objective
        → Compute frequency_7d and frequency_trend for campaigns + adsets
        → Truncate payload if over 120k chars (priority-based)
        → POST payload to Claude API → receive analysis JSON
        → POST payload to OpenAI API → receive analysis JSON (optional)
        → Query previous report for comparison deltas
        → Generate PDF with ReportLab (snapshot + data tables + model analyses + comparison)
        → Store in Postgres (raw_metrics, analyses, PDF bytes, summary stats)
        → Send email with PDF attachment
        → Update row (status: completed)
    → Frontend polls until complete
    → User views report in dashboard or downloads PDF/JSON
```

---

## File tree (new files only)

```
backend/
    services/
        meta_audit.py          # Data fetching, summarization, AI analysis
        audit_pdf.py           # PDF report generation with ReportLab
    routers/
        audit.py               # Audit trigger + report endpoints
        accounts.py            # Ad account CRUD + token test endpoint
    migrations/
        003_ad_accounts.sql    # Ad accounts table
        004_audit_reports.sql  # Audit reports table

frontend/src/
    pages/
        AuditPage.tsx          # Main audit tab with trigger + report list + detail
        AccountsPage.tsx       # Account management CRUD page
    components/
        AccountList.tsx        # Account table with status badges
        AccountForm.tsx        # Add/edit account modal with token test
        AuditTrigger.tsx       # Account selector + run audit button + model checkboxes
        AuditReportList.tsx    # Table of past reports with account column + delta badges
        AuditReportDetail.tsx  # Full report view with snapshot bar + data tables + AI tabs
        AuditSnapshot.tsx      # Horizontal metric cards with delta badges
        AuditDataTables.tsx    # Collapsible campaign/adset/ad/placement/demographic tables
        AuditModelTab.tsx      # Single model's analysis rendered as cards
        AuditCampaignVerdicts.tsx  # Campaign verdict card grid
        AuditCreativeAnalysis.tsx  # Creative format performance + fatigue signals
        AuditPlacementAnalysis.tsx # Placement performance cards
        AuditDemographicAnalysis.tsx # Demographic segment cards
        AuditComparison.tsx    # Side-by-side delta view from previous report
```
