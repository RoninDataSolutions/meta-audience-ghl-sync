# Conversion Tracking Pipeline — Build Spec

Sends clean, health-context-free purchase conversion events to Meta's Conversions API by joining Stripe payment data with GHL contact attribution data. Includes real-time tracking (Stripe webhook), historical backfill, fuzzy name matching for cross-system identity resolution, and persistent storage for all matched conversions.

---

## Why this exists

YogiSoul's domain (yogisoul.yoga) is classified as "Health & wellness" by Meta, which blocks bottom-of-funnel events (Purchase, Lead, InitiateCheckout) from being used for campaign optimization. Any pixel-based tracking or standard CAPI events associated with the flagged domain are silently dropped. The restriction is on the **domain classification**, not on the ability to send conversion events.

This pipeline bypasses the restriction entirely by using Meta's Conversions API **server-to-server** with a clean dataset that has no association with the health-classified domain. No Meta Pixel is installed on any website. No domain is referenced in the event payload. The events contain **zero health information** — only hashed PII, fbclid, a neutral custom event name, purchase value, and currency. No product names, no URLs, no health-related parameters. Meta receives a clean conversion signal it can use for optimization without ever learning what was purchased or that the business is health-related.

---

## Architecture

This feature integrates into the existing GHL → Meta Sync app. No new containers or ports.

```
Stripe webhook (checkout.session.completed)
    → FastAPI endpoint receives payment data
    → Extract: customer email, phone, name, amount, currency, timestamp
    → Match to GHL contact (email exact → phone exact → fuzzy name)
    → Pull attribution from GHL: fbclid, fbp, utm_source, utm_campaign
    → Scrub: remove ALL health context (product names, URLs, categories)
    → Build clean CAPI payload: hashed PII + fbclid + neutral event + value
    → POST to Meta Conversions API
    → Store matched conversion in Postgres for history + deduplication
```

---

## Meta CAPI setup (no pixel installation required)

This pipeline uses the Conversions API directly — server-to-server. No Meta Pixel JavaScript is installed on any website. No domain verification is required for sending events. The term "Pixel" in Meta's UI is misleading — it's really just a **dataset ID** (an event destination). You create one in Events Manager, get an ID and a token, and your server sends events to it via HTTP POST.

### Step-by-step setup in Meta

1. Go to **Events Manager** in Meta Business Suite
2. Click **"Connect Data Sources"** → select **"Web"** → name it something clean like "Yogisoul Conversions" or "RDS Conversions" — avoid words like "prenatal," "yoga," "health," or "wellness" in the dataset name
3. Meta creates a new dataset with an ID (e.g., `987654321012345`) — this is your `META_PIXEL_ID` even though no pixel is being installed
4. **Skip the pixel installation step entirely** — you don't need to add any code to any website
5. Go to **Settings** on the new dataset → scroll to **Conversions API** → click **"Generate Access Token"** → copy it
6. If Meta asks about domain verification during setup, you can skip it or come back to it later — CAPI events can be sent without a verified domain, but campaign optimization toward custom events may require verification (test this — see below)

### Testing before building

Before building the full pipeline, verify Meta accepts events on the new dataset:

```bash
curl -X POST "https://graph.facebook.com/v21.0/YOUR_DATASET_ID/events" \
  -H "Content-Type: application/json" \
  -d '{
    "data": [{
      "event_name": "evt_complete",
      "event_time": '$(date +%s)',
      "action_source": "website",
      "user_data": {
        "em": ["'$(echo -n "test@example.com" | shasum -a 256 | cut -d" " -f1)'"]
      },
      "custom_data": {
        "value": 159.00,
        "currency": "USD"
      }
    }],
    "access_token": "YOUR_CAPI_TOKEN"
  }'
```

Check Events Manager → **Test Events** tab. If the event shows up and isn't flagged or dropped, you're clear. If Meta requires a verified domain before the events are usable for optimization, register a cheap clean domain (e.g., `rds-conversions.com` — $12/year), verify it with a DNS TXT record, associate it with the dataset, and you're done. No website, no hosting, no pixel installation — just a domain record.

### If domain verification IS required

The domain must have no health-related content and no health-related name. Good examples: `rds-track.com`, `ronin-events.com`, `checkout-verify.com`. The domain doesn't need a website — it just needs to exist in DNS with Meta's verification TXT record. No hosting costs.

### New environment variables

Add to `.env`:

```env
# Meta Conversions API — dataset ID + token from Events Manager (see setup steps above)
META_CAPI_DATASET_ID=987654321012345
META_CAPI_ACCESS_TOKEN=EAAGz...

# Conversion settings
CAPI_EVENT_NAME=evt_complete              # Neutral event name — no health context

# Matching settings
FUZZY_MATCH_THRESHOLD=82                  # Minimum score (0-100) for fuzzy name matching, default 82
```

Note: The code uses `META_PIXEL_ID` internally because that's what Meta's API calls the parameter, but it's the same value as `META_CAPI_DATASET_ID` — the dataset ID created above. Map it in config:

```python
# In config.py or settings
META_PIXEL_ID = os.getenv("META_CAPI_DATASET_ID", "")
```

---

## Database

### Table: `stripe_transactions`

Complete ledger of every Stripe transaction — ALL data preserved including product names, line items, and metadata. This is your source of truth for LTV, purchase history, and product analytics. This data **never** goes to Meta — the CAPI pipeline reads only the clean fields (amount, currency, customer identifiers) from the `matched_conversions` table.

```sql
CREATE TABLE IF NOT EXISTS stripe_transactions (
    id                  SERIAL PRIMARY KEY,

    -- Stripe identifiers
    stripe_payment_id   VARCHAR(255) NOT NULL UNIQUE,  -- charge ID, payment_intent ID, or checkout session ID
    stripe_customer_id  VARCHAR(255),
    stripe_session_id   VARCHAR(255),
    stripe_invoice_id   VARCHAR(255),

    -- Customer info (as provided to Stripe)
    customer_email      VARCHAR(255),
    customer_phone      VARCHAR(50),
    customer_name       VARCHAR(255),

    -- Payment details
    amount_cents        INTEGER NOT NULL,
    currency            VARCHAR(10) NOT NULL DEFAULT 'usd',
    status              VARCHAR(30) NOT NULL,           -- succeeded, failed, refunded, partially_refunded
    payment_method      VARCHAR(50),                    -- card, link, etc.
    stripe_created_at   TIMESTAMP NOT NULL,

    -- Product details (kept locally — NEVER sent to Meta)
    line_items          JSONB,                          -- full line items array from Stripe
    product_name        VARCHAR(255),                   -- primary product name (extracted from line items)
    product_id          VARCHAR(255),                   -- Stripe product ID
    price_id            VARCHAR(255),                   -- Stripe price ID
    quantity            INTEGER DEFAULT 1,

    -- Metadata
    stripe_metadata     JSONB,                          -- any metadata attached to the payment

    -- GHL linkage (populated by the matching pipeline)
    ghl_contact_id      VARCHAR(255),
    match_method        VARCHAR(30),
    match_status        VARCHAR(20) DEFAULT 'pending',  -- pending, matched, unmatched, manual

    -- Refund tracking
    refunded_amount     INTEGER DEFAULT 0,
    refund_date         TIMESTAMP,

    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_st_payment ON stripe_transactions(stripe_payment_id);
CREATE INDEX idx_st_customer ON stripe_transactions(stripe_customer_id);
CREATE INDEX idx_st_email ON stripe_transactions(customer_email);
CREATE INDEX idx_st_session ON stripe_transactions(stripe_session_id);
CREATE INDEX idx_st_ghl ON stripe_transactions(ghl_contact_id);
CREATE INDEX idx_st_created ON stripe_transactions(stripe_created_at DESC);
CREATE INDEX idx_st_product ON stripe_transactions(product_name);
```

### Table: `contact_ltv`

Materialized LTV per GHL contact. Updated whenever a new transaction is matched or a refund is processed.

```sql
CREATE TABLE IF NOT EXISTS contact_ltv (
    id                  SERIAL PRIMARY KEY,
    ghl_contact_id      VARCHAR(255) NOT NULL UNIQUE,
    ghl_name            VARCHAR(255),
    ghl_email           VARCHAR(255),

    -- LTV metrics
    total_revenue       DECIMAL(12,2) NOT NULL DEFAULT 0,
    total_refunds       DECIMAL(12,2) NOT NULL DEFAULT 0,
    net_revenue         DECIMAL(12,2) NOT NULL DEFAULT 0,     -- total_revenue - total_refunds
    transaction_count   INTEGER NOT NULL DEFAULT 0,
    first_purchase_at   TIMESTAMP,
    last_purchase_at    TIMESTAMP,
    avg_order_value     DECIMAL(10,2),

    -- Product breakdown (kept locally — NEVER sent to Meta)
    products_purchased  JSONB DEFAULT '[]',                   -- [{"name": "Core Pack", "count": 2, "total": 318.00}, ...]

    -- Computed fields
    days_as_customer    INTEGER,                              -- last_purchase - first_purchase
    purchase_frequency  DECIMAL(6,2),                         -- transactions per 30 days

    updated_at          TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ltv_ghl ON contact_ltv(ghl_contact_id);
CREATE INDEX idx_ltv_revenue ON contact_ltv(net_revenue DESC);
```

### Table: `matched_conversions`

Stores every conversion event sent (or attempted) to Meta. Serves three purposes: deduplication (don't send the same Stripe payment twice), historical audit trail, and match quality monitoring.

```sql
CREATE TABLE IF NOT EXISTS matched_conversions (
    id                  SERIAL PRIMARY KEY,

    -- Stripe side
    stripe_session_id   VARCHAR(255) NOT NULL UNIQUE,
    stripe_customer_id  VARCHAR(255),
    stripe_email        VARCHAR(255),
    stripe_phone        VARCHAR(50),
    stripe_name         VARCHAR(255),
    amount_cents        INTEGER NOT NULL,
    currency            VARCHAR(10) NOT NULL DEFAULT 'usd',
    stripe_created_at   TIMESTAMP NOT NULL,

    -- GHL side (null if no match found)
    ghl_contact_id      VARCHAR(255),
    ghl_email           VARCHAR(255),
    ghl_phone           VARCHAR(50),
    ghl_name            VARCHAR(255),
    ghl_fbclid          TEXT,
    ghl_fbp             TEXT,                    -- Facebook browser ID (_fbp cookie)
    ghl_utm_source      VARCHAR(255),
    ghl_utm_medium      VARCHAR(255),
    ghl_utm_campaign    VARCHAR(255),

    -- Match metadata
    match_method        VARCHAR(30),             -- 'email_exact', 'phone_exact', 'name_fuzzy', 'none'
    match_score         INTEGER,                 -- fuzzy match score (0-100), null for exact matches
    match_candidates    JSONB,                   -- top 3 fuzzy candidates with scores (for debugging)

    -- Meta CAPI status
    capi_status         VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, sent, failed, skipped
    capi_event_id       VARCHAR(255),            -- deduplication event_id sent to Meta
    capi_sent_at        TIMESTAMP,
    capi_response       JSONB,                   -- Meta's API response
    capi_error          TEXT,

    -- Source
    source              VARCHAR(20) NOT NULL DEFAULT 'webhook',  -- 'webhook' or 'backfill'

    created_at          TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mc_stripe_session ON matched_conversions(stripe_session_id);
CREATE INDEX idx_mc_ghl_contact ON matched_conversions(ghl_contact_id);
CREATE INDEX idx_mc_capi_status ON matched_conversions(capi_status);
CREATE INDEX idx_mc_created ON matched_conversions(created_at DESC);
```

### Table: `contact_identity_map`

Persistent identity resolution cache. Once a Stripe customer is matched to a GHL contact, store the mapping so future purchases from the same customer skip the matching process entirely.

```sql
CREATE TABLE IF NOT EXISTS contact_identity_map (
    id                  SERIAL PRIMARY KEY,
    stripe_customer_id  VARCHAR(255),
    stripe_email        VARCHAR(255),
    ghl_contact_id      VARCHAR(255) NOT NULL,
    match_method        VARCHAR(30) NOT NULL,
    match_score         INTEGER,
    confirmed           BOOLEAN NOT NULL DEFAULT false,  -- true if manually verified
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE(stripe_customer_id, ghl_contact_id),
    UNIQUE(stripe_email, ghl_contact_id)
);

CREATE INDEX idx_cim_stripe_customer ON contact_identity_map(stripe_customer_id);
CREATE INDEX idx_cim_stripe_email ON contact_identity_map(stripe_email);
CREATE INDEX idx_cim_ghl_contact ON contact_identity_map(ghl_contact_id);
```

---

## Backend: New files

### File: `backend/services/conversion_tracker.py`

Core module with four responsibilities: Stripe data extraction, GHL contact matching, CAPI event construction, and CAPI delivery.

---

#### 1. Stripe data extraction

When a `checkout.session.completed` webhook fires, Stripe sends a session object. Extract:

```python
def extract_stripe_data(session: dict) -> dict:
    """
    Extract customer data from a Stripe checkout session.
    Works for both webhook payloads and historical API fetches.
    """
    customer_details = session.get("customer_details", {})
    return {
        "session_id": session["id"],
        "customer_id": session.get("customer"),
        "email": (customer_details.get("email") or session.get("customer_email") or "").lower().strip(),
        "phone": normalize_phone(customer_details.get("phone") or ""),
        "name": (customer_details.get("name") or "").strip(),
        "amount_cents": session.get("amount_total", 0),
        "currency": session.get("currency", "usd").lower(),
        "created_at": datetime.utcfromtimestamp(session.get("created", 0)),
    }
```

**Phone normalization:** Strip all non-digit characters, remove leading country code if present (1 for US), store as 10-digit string. This is critical for matching — GHL might store `(214) 555-1234` while Stripe stores `+12145551234`.

```python
import re

def normalize_phone(phone: str) -> str:
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits if len(digits) >= 10 else ""
```

**Important:** Do NOT extract `line_items` or product names. The product information stays in Stripe and your database only — it never gets sent to Meta.

---

#### 2. GHL contact matching

This is the identity resolution engine. Given a Stripe customer (email, phone, name), find the corresponding GHL contact to retrieve their Meta attribution data (fbclid, fbp, UTMs).

**Match cascade — try each method in order, stop at first match:**

```
Step 0: Check contact_identity_map for a cached match (fastest)
Step 1: Exact email match against GHL contacts
Step 2: Exact phone match (after normalization) against GHL contacts
Step 3: Fuzzy name match against GHL contacts (last resort)
```

##### Step 0: Identity map lookup

```python
async def check_identity_map(stripe_customer_id: str | None, stripe_email: str) -> str | None:
    """
    Check if we've previously matched this Stripe customer to a GHL contact.
    Returns ghl_contact_id if found, None otherwise.
    """
    # Try by stripe_customer_id first (most stable identifier)
    if stripe_customer_id:
        row = await db.fetchone(
            "SELECT ghl_contact_id FROM contact_identity_map WHERE stripe_customer_id = $1",
            stripe_customer_id
        )
        if row:
            return row["ghl_contact_id"]

    # Fall back to email
    if stripe_email:
        row = await db.fetchone(
            "SELECT ghl_contact_id FROM contact_identity_map WHERE stripe_email = $1",
            stripe_email
        )
        if row:
            return row["ghl_contact_id"]

    return None
```

##### Step 1: Exact email match

```python
async def match_by_email(email: str, ghl_contacts: list[dict]) -> dict | None:
    """Find GHL contact with exact email match (case-insensitive)."""
    email_lower = email.lower().strip()
    for contact in ghl_contacts:
        contact_email = (contact.get("email") or "").lower().strip()
        if contact_email and contact_email == email_lower:
            return contact

        # Also check additionalEmails field
        for alt in contact.get("additionalEmails", []):
            if alt.lower().strip() == email_lower:
                return contact

    return None
```

##### Step 2: Exact phone match

```python
async def match_by_phone(phone: str, ghl_contacts: list[dict]) -> dict | None:
    """Find GHL contact with exact phone match after normalization."""
    if not phone or len(phone) < 10:
        return None

    for contact in ghl_contacts:
        contact_phone = normalize_phone(contact.get("phone") or "")
        if contact_phone and contact_phone == phone:
            return contact

        # Also check additionalPhones
        for alt in contact.get("additionalPhones", []):
            if normalize_phone(alt) == phone:
                return contact

    return None
```

##### Step 3: Fuzzy name match

This handles the case where a customer uses a different email/phone in Stripe than in GHL (e.g., personal email for payment, business email for the GHL form), but the name is similar.

Use `thefuzz` library (formerly `fuzzywuzzy`) for string matching:

```
pip install thefuzz python-Levenshtein
```

```python
from thefuzz import fuzz

def fuzzy_name_score(name_a: str, name_b: str) -> int:
    """
    Score how similar two names are (0-100).
    Uses multiple strategies and takes the best score.
    """
    if not name_a or not name_b:
        return 0

    a = name_a.lower().strip()
    b = name_b.lower().strip()

    # Exact match
    if a == b:
        return 100

    scores = []

    # Full string ratio
    scores.append(fuzz.ratio(a, b))

    # Token sort (handles "John Smith" vs "Smith John")
    scores.append(fuzz.token_sort_ratio(a, b))

    # Token set (handles "John A Smith" vs "John Smith")
    scores.append(fuzz.token_set_ratio(a, b))

    # Partial ratio (handles "John" vs "John Smith")
    scores.append(fuzz.partial_ratio(a, b))

    # First name match (strong signal for common first names)
    a_parts = a.split()
    b_parts = b.split()
    if a_parts and b_parts and a_parts[0] == b_parts[0]:
        scores.append(85)  # Boost for matching first name

    return max(scores)


async def match_by_name_fuzzy(
    name: str,
    ghl_contacts: list[dict],
    threshold: int = 82
) -> tuple[dict | None, int, list[dict]]:
    """
    Fuzzy match a name against all GHL contacts.
    Returns: (best_match_contact, score, top_3_candidates)
    """
    if not name:
        return None, 0, []

    candidates = []
    for contact in ghl_contacts:
        # Build the GHL contact's full name
        ghl_first = (contact.get("firstName") or "").strip()
        ghl_last = (contact.get("lastName") or "").strip()
        ghl_full = f"{ghl_first} {ghl_last}".strip()
        ghl_name_field = (contact.get("name") or "").strip()

        # Score against both the constructed name and the name field
        score_full = fuzzy_name_score(name, ghl_full) if ghl_full else 0
        score_name = fuzzy_name_score(name, ghl_name_field) if ghl_name_field else 0
        best_score = max(score_full, score_name)

        if best_score >= 50:  # Only consider reasonable candidates
            candidates.append({
                "contact_id": contact.get("id"),
                "ghl_name": ghl_full or ghl_name_field,
                "score": best_score,
                "contact": contact,
            })

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top_3 = [{"name": c["ghl_name"], "score": c["score"], "id": c["contact_id"]}
             for c in candidates[:3]]

    if candidates and candidates[0]["score"] >= threshold:
        return candidates[0]["contact"], candidates[0]["score"], top_3

    return None, 0, top_3
```

**Threshold guidance:** 82 is conservative — it catches "Radhika Patel" vs "Radhika K Patel" (token set ratio ~90) but rejects "Radhika" vs "Priya" (ratio ~30). If match quality is too low, raise to 88. If too many near-misses, lower to 75 but add a manual review queue (see the `confirmed` field on `contact_identity_map`).

##### Full matching pipeline

```python
async def match_stripe_to_ghl(stripe_data: dict) -> dict:
    """
    Run the full match cascade. Returns match result with method and score.
    """
    result = {
        "ghl_contact": None,
        "match_method": "none",
        "match_score": None,
        "match_candidates": [],
    }

    # Step 0: Check cached identity map
    cached_ghl_id = await check_identity_map(
        stripe_data["customer_id"],
        stripe_data["email"]
    )
    if cached_ghl_id:
        contact = await fetch_ghl_contact_by_id(cached_ghl_id)
        if contact:
            result["ghl_contact"] = contact
            result["match_method"] = "identity_map"
            return result

    # Fetch all GHL contacts (use the cached version from ghl_client.py)
    ghl_contacts = await get_all_contacts()

    # Step 1: Exact email
    if stripe_data["email"]:
        contact = await match_by_email(stripe_data["email"], ghl_contacts)
        if contact:
            result["ghl_contact"] = contact
            result["match_method"] = "email_exact"
            await save_identity_map(stripe_data, contact, "email_exact")
            return result

    # Step 2: Exact phone
    if stripe_data["phone"]:
        contact = await match_by_phone(stripe_data["phone"], ghl_contacts)
        if contact:
            result["ghl_contact"] = contact
            result["match_method"] = "phone_exact"
            await save_identity_map(stripe_data, contact, "phone_exact")
            return result

    # Step 3: Fuzzy name
    if stripe_data["name"]:
        threshold = int(os.getenv("FUZZY_MATCH_THRESHOLD", "82"))
        contact, score, candidates = await match_by_name_fuzzy(
            stripe_data["name"], ghl_contacts, threshold
        )
        result["match_candidates"] = candidates
        if contact:
            result["ghl_contact"] = contact
            result["match_method"] = "name_fuzzy"
            result["match_score"] = score
            await save_identity_map(stripe_data, contact, "name_fuzzy", score)
            return result

    return result
```

---

#### 3. GHL attribution extraction

Once a GHL contact is matched, extract the Meta attribution fields. GHL stores these in custom fields and/or the contact's `attributionSource` data.

```python
def extract_ghl_attribution(contact: dict) -> dict:
    """
    Extract Meta attribution data from a GHL contact record.
    Looks in custom fields, attribution source, and tags.
    """
    custom_fields = {
        cf.get("id"): cf.get("value")
        for cf in contact.get("customFields", [])
        if cf.get("value")
    }

    # fbclid may be in:
    # 1. A custom field explicitly named fbclid
    # 2. The contact's attributionSource.fbclid
    # 3. The contact's source URL query parameters
    attribution = contact.get("attributionSource", {}) or {}
    source_url = attribution.get("url") or ""

    fbclid = (
        _find_custom_field(contact, "fbclid")
        or attribution.get("fbclid")
        or _extract_param(source_url, "fbclid")
    )

    fbp = (
        _find_custom_field(contact, "fbp")
        or _find_custom_field(contact, "_fbp")
        or attribution.get("fbp")
    )

    return {
        "fbclid": fbclid,
        "fbp": fbp,
        "utm_source": attribution.get("utmSource") or _extract_param(source_url, "utm_source"),
        "utm_medium": attribution.get("utmMedium") or _extract_param(source_url, "utm_medium"),
        "utm_campaign": attribution.get("utmCampaign") or _extract_param(source_url, "utm_campaign"),
        "email": (contact.get("email") or "").lower().strip(),
        "phone": normalize_phone(contact.get("phone") or ""),
        "first_name": (contact.get("firstName") or "").strip(),
        "last_name": (contact.get("lastName") or "").strip(),
        "city": (contact.get("city") or "").strip(),
        "state": (contact.get("state") or "").strip(),
        "zip": (contact.get("postalCode") or "").strip(),
        "country": (contact.get("country") or "US").strip(),
    }


def _find_custom_field(contact: dict, field_name: str) -> str | None:
    """Search custom fields by name (case-insensitive)."""
    for cf in contact.get("customFields", []):
        if cf.get("name", "").lower().replace(" ", "_") == field_name.lower():
            return cf.get("value")
    return None


def _extract_param(url: str, param: str) -> str | None:
    """Extract a query parameter from a URL."""
    from urllib.parse import urlparse, parse_qs
    try:
        parsed = urlparse(url)
        values = parse_qs(parsed.query).get(param, [])
        return values[0] if values else None
    except Exception:
        return None
```

---

#### 4. CAPI event construction and delivery

Build the Meta Conversions API payload with **zero health context**.

```python
import hashlib
import time
import uuid


def sha256_hash(value: str) -> str:
    """Hash a value with SHA-256 for Meta CAPI."""
    return hashlib.sha256(value.lower().strip().encode()).hexdigest()


def build_capi_event(stripe_data: dict, ghl_attribution: dict) -> dict:
    """
    Build a Meta Conversions API event payload.

    CRITICAL: This payload must contain ZERO health-related information.
    No product names, no URLs from the health-classified domain,
    no category names, no class types, nothing that reveals what
    was purchased — only THAT a purchase happened and HOW MUCH it was.
    """
    event_name = os.getenv("CAPI_EVENT_NAME", "evt_complete")
    event_id = f"evt_{stripe_data['session_id']}_{uuid.uuid4().hex[:8]}"

    # User data — all hashed except fbclid/fbp (Meta requires these raw)
    user_data = {}

    # Email (hash it)
    email = ghl_attribution.get("email") or stripe_data.get("email")
    if email:
        user_data["em"] = [sha256_hash(email)]

    # Phone (hash it, with country code)
    phone = ghl_attribution.get("phone") or stripe_data.get("phone")
    if phone and len(phone) >= 10:
        user_data["ph"] = [sha256_hash(f"1{phone}")]  # Prepend US country code

    # Name (hash first and last separately)
    fn = ghl_attribution.get("first_name") or stripe_data.get("name", "").split()[0] if stripe_data.get("name") else ""
    ln = ghl_attribution.get("last_name") or (stripe_data.get("name", "").split()[-1] if len(stripe_data.get("name", "").split()) > 1 else "")
    if fn:
        user_data["fn"] = [sha256_hash(fn)]
    if ln:
        user_data["ln"] = [sha256_hash(ln)]

    # Location (hash it)
    if ghl_attribution.get("city"):
        user_data["ct"] = [sha256_hash(ghl_attribution["city"])]
    if ghl_attribution.get("state"):
        user_data["st"] = [sha256_hash(ghl_attribution["state"])]
    if ghl_attribution.get("zip"):
        user_data["zp"] = [sha256_hash(ghl_attribution["zip"])]
    if ghl_attribution.get("country"):
        user_data["country"] = [sha256_hash(ghl_attribution["country"])]

    # Attribution identifiers (NOT hashed — Meta needs these raw)
    if ghl_attribution.get("fbclid"):
        user_data["fbc"] = f"fb.1.{int(time.time())}.{ghl_attribution['fbclid']}"
    if ghl_attribution.get("fbp"):
        user_data["fbp"] = ghl_attribution["fbp"]

    # Build the event
    event = {
        "event_name": event_name,
        "event_time": int(stripe_data["created_at"].timestamp()),
        "event_id": event_id,
        "action_source": "website",
        "user_data": user_data,
        "custom_data": {
            "value": stripe_data["amount_cents"] / 100,
            "currency": stripe_data["currency"].upper(),
            # NO content_name, NO content_category, NO content_type
            # NO product ID, NO product name — these would reveal health context
        },
    }

    # No event_source_url — we are not using a pixel on any website.
    # Omitting this field entirely is valid per Meta's CAPI spec.
    # This avoids any association with the health-classified yogisoul.yoga domain.

    return event, event_id


async def send_to_meta_capi(event: dict, pixel_id: str, access_token: str) -> dict:
    """
    POST the event to Meta's Conversions API.
    Endpoint: POST /{pixel_id}/events
    """
    import httpx

    url = f"https://graph.facebook.com/v21.0/{pixel_id}/events"

    payload = {
        "data": [event],
        "access_token": access_token,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        result = resp.json()

        if resp.status_code != 200:
            raise Exception(f"CAPI error {resp.status_code}: {result}")

        return result
```

---

#### 5. Full pipeline orchestration

```python
async def process_conversion(stripe_session: dict, source: str = "webhook") -> dict:
    """
    Full pipeline: extract Stripe data → match GHL contact → send CAPI event.
    Returns the matched_conversions row data.
    """
    # 1. Check deduplication
    existing = await db.fetchone(
        "SELECT id, capi_status FROM matched_conversions WHERE stripe_session_id = $1",
        stripe_session["id"]
    )
    if existing:
        logger.info(f"Duplicate Stripe session {stripe_session['id']}, skipping")
        return {"status": "duplicate", "id": existing["id"]}

    # 2. Extract Stripe data
    stripe_data = extract_stripe_data(stripe_session)

    # 3. Match to GHL contact
    match_result = await match_stripe_to_ghl(stripe_data)
    ghl_contact = match_result["ghl_contact"]

    # 4. Extract attribution (empty dict if no match)
    ghl_attribution = {}
    if ghl_contact:
        ghl_attribution = extract_ghl_attribution(ghl_contact)

    # 5. Build CAPI event
    event, event_id = build_capi_event(stripe_data, ghl_attribution)

    # 6. Insert record (before sending — ensures we track even if CAPI fails)
    row = {
        "stripe_session_id": stripe_data["session_id"],
        "stripe_customer_id": stripe_data["customer_id"],
        "stripe_email": stripe_data["email"],
        "stripe_phone": stripe_data["phone"],
        "stripe_name": stripe_data["name"],
        "amount_cents": stripe_data["amount_cents"],
        "currency": stripe_data["currency"],
        "stripe_created_at": stripe_data["created_at"],
        "ghl_contact_id": ghl_contact.get("id") if ghl_contact else None,
        "ghl_email": ghl_attribution.get("email"),
        "ghl_phone": ghl_attribution.get("phone"),
        "ghl_name": f"{ghl_attribution.get('first_name', '')} {ghl_attribution.get('last_name', '')}".strip() if ghl_contact else None,
        "ghl_fbclid": ghl_attribution.get("fbclid"),
        "ghl_fbp": ghl_attribution.get("fbp"),
        "ghl_utm_source": ghl_attribution.get("utm_source"),
        "ghl_utm_medium": ghl_attribution.get("utm_medium"),
        "ghl_utm_campaign": ghl_attribution.get("utm_campaign"),
        "match_method": match_result["match_method"],
        "match_score": match_result.get("match_score"),
        "match_candidates": match_result.get("match_candidates"),
        "capi_event_id": event_id,
        "capi_status": "pending",
        "source": source,
    }
    row_id = await db.insert("matched_conversions", row)

    # 7. Send to Meta CAPI
    # Even if no GHL match, still send — Meta can match on hashed email/phone alone
    pixel_id = settings.META_PIXEL_ID
    capi_token = settings.META_CAPI_ACCESS_TOKEN

    if not pixel_id or not capi_token:
        await db.update("matched_conversions", row_id, {
            "capi_status": "skipped",
            "capi_error": "META_PIXEL_ID or META_CAPI_ACCESS_TOKEN not configured",
        })
        return {"status": "skipped", "id": row_id, "reason": "no CAPI credentials"}

    try:
        response = await send_to_meta_capi(event, pixel_id, capi_token)
        await db.update("matched_conversions", row_id, {
            "capi_status": "sent",
            "capi_sent_at": datetime.utcnow(),
            "capi_response": response,
        })
        logger.info(
            f"CAPI event sent: {event_id} | match={match_result['match_method']} | "
            f"amount=${stripe_data['amount_cents']/100:.2f} | "
            f"has_fbclid={'yes' if ghl_attribution.get('fbclid') else 'no'}"
        )
        return {"status": "sent", "id": row_id, "match": match_result["match_method"]}

    except Exception as e:
        await db.update("matched_conversions", row_id, {
            "capi_status": "failed",
            "capi_error": str(e),
        })
        logger.error(f"CAPI send failed for {stripe_data['session_id']}: {e}")
        return {"status": "failed", "id": row_id, "error": str(e)}
```

---

### File: `backend/routers/conversions.py`

New FastAPI router mounted at `/api/conversions`.

#### Endpoints

**`POST /api/webhooks/stripe-conversion`**

Stripe webhook endpoint for real-time conversion tracking. Configure in Stripe Dashboard → Developers → Webhooks → Add endpoint.

Event types to listen for: `checkout.session.completed`

```python
@router.post("/webhooks/stripe-conversion")
async def stripe_conversion_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    # Verify webhook signature
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        raise HTTPException(400, f"Invalid signature: {e}")

    if event["type"] != "checkout.session.completed":
        return {"status": "ignored", "type": event["type"]}

    session = event["data"]["object"]
    result = await process_conversion(session, source="webhook")
    return result
```

**`POST /api/conversions/backfill`**

Backfill historical Stripe purchases. Pulls all checkout sessions from Stripe for the given date range and processes each one.

Request body:
```json
{
    "days_back": 90,
    "limit": 500,
    "dry_run": false
}
```

Response:
```json
{
    "status": "completed",
    "total_sessions": 47,
    "matched": 38,
    "sent_to_capi": 35,
    "failed": 3,
    "duplicates_skipped": 2,
    "no_match": 9,
    "match_breakdown": {
        "identity_map": 5,
        "email_exact": 28,
        "phone_exact": 3,
        "name_fuzzy": 2,
        "none": 9
    }
}
```

Implementation:
```python
@router.post("/backfill")
async def backfill_conversions(body: BackfillRequest, background_tasks: BackgroundTasks):
    """Kick off backfill in background."""
    # Create a status record
    job_id = await create_backfill_job(body.days_back, body.limit)
    background_tasks.add_task(run_backfill, job_id, body.days_back, body.limit, body.dry_run)
    return {"status": "started", "job_id": job_id}


async def run_backfill(job_id: int, days_back: int, limit: int, dry_run: bool):
    """
    Fetch historical Stripe sessions and process each.
    For events older than 7 days, Meta's attribution is weaker but still useful.
    For events older than 28 days, use Offline Events API instead of CAPI.
    """
    import stripe as stripe_lib
    stripe_lib.api_key = settings.STRIPE_SECRET_KEY

    after_ts = int((datetime.utcnow() - timedelta(days=days_back)).timestamp())
    sessions = []
    has_more = True
    starting_after = None

    while has_more and len(sessions) < limit:
        params = {
            "limit": 100,
            "created": {"gte": after_ts},
            "status": "complete",
            "expand": ["data.customer_details"],
        }
        if starting_after:
            params["starting_after"] = starting_after

        batch = stripe_lib.checkout.Session.list(**params)
        sessions.extend(batch.data)
        has_more = batch.has_more
        if batch.data:
            starting_after = batch.data[-1].id

    stats = {"total": len(sessions), "matched": 0, "sent": 0, "failed": 0, "skipped": 0}

    for session in sessions:
        result = await process_conversion(session.to_dict(), source="backfill")
        if result["status"] == "sent":
            stats["sent"] += 1
            stats["matched"] += 1
        elif result["status"] == "duplicate":
            stats["skipped"] += 1
        elif result["status"] == "failed":
            stats["failed"] += 1
        else:
            stats["matched"] += 1 if result.get("match") != "none" else 0

    await update_backfill_job(job_id, stats)
```

**`POST /api/transactions/sync`**

Full Stripe transaction history pull. Unlike the CAPI backfill (which only pulls Checkout Sessions), this pulls ALL payment types — charges, payment intents, invoices — going back to the beginning of the Stripe account. Stores everything in `stripe_transactions` with full product details, then matches each to a GHL contact and computes LTV.

This is the data foundation. Run it once to build the complete ledger, then the webhook keeps it current going forward.

Request body:
```json
{
    "days_back": null,
    "limit": 5000
}
```

Set `days_back` to `null` to pull everything from account creation. Set a number to limit the window.

Response:
```json
{
    "status": "completed",
    "total_payments": 142,
    "new_stored": 138,
    "duplicates_skipped": 4,
    "matched_to_ghl": 112,
    "unmatched": 30,
    "total_revenue": 18647.00,
    "unique_customers": 87,
    "products_found": ["Drop-in", "Starter Pack", "Core Pack", "Full Journey", "Foundations", "Deep Dive"],
    "ltv_computed_for": 112
}
```

Implementation:

```python
@router.post("/transactions/sync")
async def sync_stripe_transactions(body: TransactionSyncRequest, background_tasks: BackgroundTasks):
    """Pull full Stripe history and build the transaction ledger."""
    job_id = await create_sync_job("stripe_transactions")
    background_tasks.add_task(run_transaction_sync, job_id, body.days_back, body.limit)
    return {"status": "started", "job_id": job_id}


async def run_transaction_sync(job_id: int, days_back: int | None, limit: int):
    """
    Pull ALL Stripe payments — not just Checkout Sessions.
    
    Early payments from solo operators often bypass Checkout Sessions entirely
    (manual charges, invoice payments, direct payment intents). This catches everything.
    
    Pull order:
    1. Payment Intents (most common for recent payments)
    2. Charges (catches older payments and direct charges)
    3. Deduplicate by payment_intent → charge linkage
    """
    import stripe as stripe_lib
    stripe_lib.api_key = settings.STRIPE_SECRET_KEY

    params = {"limit": 100, "status": "succeeded"}
    if days_back:
        params["created"] = {"gte": int((datetime.utcnow() - timedelta(days=days_back)).timestamp())}

    # --- Pull Payment Intents ---
    all_payments = []
    has_more = True
    starting_after = None

    while has_more and len(all_payments) < limit:
        p = {**params}
        if starting_after:
            p["starting_after"] = starting_after
        
        batch = stripe_lib.PaymentIntent.list(
            **p,
            expand=["data.latest_charge", "data.customer"],
        )
        for pi in batch.data:
            all_payments.append(await _normalize_payment_intent(pi))
        has_more = batch.has_more
        if batch.data:
            starting_after = batch.data[-1].id

    # --- Pull Charges (catches payments without PaymentIntents) ---
    starting_after = None
    has_more = True
    seen_charge_ids = {p.get("charge_id") for p in all_payments if p.get("charge_id")}

    while has_more and len(all_payments) < limit:
        p = {"limit": 100}
        if days_back:
            p["created"] = params["created"]
        if starting_after:
            p["starting_after"] = starting_after

        batch = stripe_lib.Charge.list(**p)
        for charge in batch.data:
            if charge.status != "succeeded":
                continue
            if charge.id in seen_charge_ids:
                continue  # Already captured via PaymentIntent
            all_payments.append(await _normalize_charge(charge))
        has_more = batch.has_more
        if batch.data:
            starting_after = batch.data[-1].id

    # --- Store and match each payment ---
    ghl_contacts = await get_all_contacts()  # Fetch once, match many
    stats = {"total": len(all_payments), "new": 0, "matched": 0, "skipped": 0}

    for payment in all_payments:
        # Deduplicate
        existing = await db.fetchone(
            "SELECT id FROM stripe_transactions WHERE stripe_payment_id = $1",
            payment["payment_id"]
        )
        if existing:
            stats["skipped"] += 1
            continue

        # Fetch line items if this was a Checkout Session
        line_items = []
        product_name = None
        product_id = None
        price_id = None

        if payment.get("session_id"):
            try:
                items = stripe_lib.checkout.Session.list_line_items(
                    payment["session_id"], limit=10
                )
                line_items = [item.to_dict() for item in items.data]
                if line_items:
                    first = line_items[0]
                    product_name = first.get("description", "")
                    price_data = first.get("price", {})
                    product_id = price_data.get("product") if isinstance(price_data, dict) else None
                    price_id = price_data.get("id") if isinstance(price_data, dict) else None
            except Exception as e:
                logger.warning(f"Could not fetch line items for {payment['session_id']}: {e}")

        # If no line items from session, try to get product from charge metadata or description
        if not product_name:
            product_name = (
                payment.get("metadata", {}).get("product_name")
                or payment.get("description")
                or ""
            )

        # Match to GHL contact
        match_result = await match_stripe_to_ghl({
            "customer_id": payment.get("customer_id"),
            "email": payment.get("email", ""),
            "phone": payment.get("phone", ""),
            "name": payment.get("name", ""),
        })
        ghl_contact = match_result["ghl_contact"]

        # Store transaction
        await db.insert("stripe_transactions", {
            "stripe_payment_id": payment["payment_id"],
            "stripe_customer_id": payment.get("customer_id"),
            "stripe_session_id": payment.get("session_id"),
            "stripe_invoice_id": payment.get("invoice_id"),
            "customer_email": payment.get("email"),
            "customer_phone": payment.get("phone"),
            "customer_name": payment.get("name"),
            "amount_cents": payment["amount_cents"],
            "currency": payment["currency"],
            "status": "succeeded",
            "payment_method": payment.get("payment_method_type"),
            "stripe_created_at": payment["created_at"],
            "line_items": line_items,
            "product_name": product_name,
            "product_id": product_id,
            "price_id": price_id,
            "quantity": line_items[0].get("quantity", 1) if line_items else 1,
            "stripe_metadata": payment.get("metadata", {}),
            "ghl_contact_id": ghl_contact.get("id") if ghl_contact else None,
            "match_method": match_result["match_method"],
            "match_status": "matched" if ghl_contact else "unmatched",
        })
        stats["new"] += 1
        if ghl_contact:
            stats["matched"] += 1

    # --- Recompute LTV for all matched contacts ---
    await recompute_all_ltv()

    await update_sync_job(job_id, stats)


async def _normalize_payment_intent(pi) -> dict:
    """Normalize a PaymentIntent into the common payment format."""
    charge = pi.latest_charge if hasattr(pi, 'latest_charge') and pi.latest_charge else {}
    if isinstance(charge, str):
        charge = {}
    customer = pi.customer if hasattr(pi, 'customer') else None
    customer_obj = customer if isinstance(customer, dict) else {}

    return {
        "payment_id": pi.id,
        "charge_id": charge.id if hasattr(charge, 'id') else None,
        "customer_id": customer_obj.get("id") or (customer if isinstance(customer, str) else None),
        "session_id": pi.metadata.get("checkout_session") if pi.metadata else None,
        "invoice_id": pi.invoice if isinstance(pi.invoice, str) else None,
        "email": (charge.billing_details.email if hasattr(charge, 'billing_details') and charge.billing_details else None)
                 or customer_obj.get("email") or "",
        "phone": (charge.billing_details.phone if hasattr(charge, 'billing_details') and charge.billing_details else None)
                 or customer_obj.get("phone") or "",
        "name": (charge.billing_details.name if hasattr(charge, 'billing_details') and charge.billing_details else None)
                or customer_obj.get("name") or "",
        "amount_cents": pi.amount_received or pi.amount,
        "currency": pi.currency,
        "payment_method_type": pi.payment_method_types[0] if pi.payment_method_types else None,
        "created_at": datetime.utcfromtimestamp(pi.created),
        "metadata": dict(pi.metadata) if pi.metadata else {},
        "description": pi.description or "",
    }


async def _normalize_charge(charge) -> dict:
    """Normalize a standalone Charge into the common payment format."""
    bd = charge.billing_details if hasattr(charge, 'billing_details') else {}
    return {
        "payment_id": charge.id,
        "charge_id": charge.id,
        "customer_id": charge.customer if isinstance(charge.customer, str) else None,
        "session_id": None,
        "invoice_id": charge.invoice if isinstance(charge.invoice, str) else None,
        "email": (bd.email if bd else None) or "",
        "phone": (bd.phone if bd else None) or "",
        "name": (bd.name if bd else None) or "",
        "amount_cents": charge.amount,
        "currency": charge.currency,
        "payment_method_type": charge.payment_method_details.type if hasattr(charge, 'payment_method_details') and charge.payment_method_details else None,
        "created_at": datetime.utcfromtimestamp(charge.created),
        "metadata": dict(charge.metadata) if charge.metadata else {},
        "description": charge.description or "",
    }
```

**`POST /api/transactions/recompute-ltv`**

Recompute LTV for all matched contacts from the `stripe_transactions` table. Run this after a backfill, after manual match corrections, or on a schedule.

```python
async def recompute_all_ltv():
    """
    Recompute contact_ltv for every GHL contact that has at least one
    matched transaction in stripe_transactions.
    """
    rows = await db.fetch("""
        SELECT
            ghl_contact_id,
            SUM(amount_cents) as total_cents,
            SUM(refunded_amount) as total_refund_cents,
            COUNT(*) as txn_count,
            MIN(stripe_created_at) as first_purchase,
            MAX(stripe_created_at) as last_purchase,
            json_agg(json_build_object(
                'name', product_name,
                'amount', amount_cents / 100.0,
                'date', stripe_created_at
            )) as products
        FROM stripe_transactions
        WHERE ghl_contact_id IS NOT NULL
          AND status = 'succeeded'
        GROUP BY ghl_contact_id
    """)

    for row in rows:
        ghl_id = row["ghl_contact_id"]
        total_rev = row["total_cents"] / 100.0
        total_refunds = (row["total_refund_cents"] or 0) / 100.0
        net = total_rev - total_refunds
        txn_count = row["txn_count"]
        first = row["first_purchase"]
        last = row["last_purchase"]
        days = (last - first).days if first and last else 0
        frequency = (txn_count / max(days, 1)) * 30 if days > 0 else 0
        aov = net / txn_count if txn_count else 0

        # Build product summary
        products = row["products"] or []
        product_summary = {}
        for p in products:
            name = p.get("name") or "Unknown"
            if name not in product_summary:
                product_summary[name] = {"name": name, "count": 0, "total": 0}
            product_summary[name]["count"] += 1
            product_summary[name]["total"] += p.get("amount", 0)

        # Fetch contact info for display
        contact = await fetch_ghl_contact_by_id(ghl_id)
        ghl_name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip() if contact else ""
        ghl_email = contact.get("email", "") if contact else ""

        await db.upsert("contact_ltv", "ghl_contact_id", {
            "ghl_contact_id": ghl_id,
            "ghl_name": ghl_name,
            "ghl_email": ghl_email,
            "total_revenue": total_rev,
            "total_refunds": total_refunds,
            "net_revenue": net,
            "transaction_count": txn_count,
            "first_purchase_at": first,
            "last_purchase_at": last,
            "avg_order_value": round(aov, 2),
            "products_purchased": list(product_summary.values()),
            "days_as_customer": days,
            "purchase_frequency": round(frequency, 2),
            "updated_at": datetime.utcnow(),
        })
```

**`GET /api/transactions`**

List all stored transactions with filters.

Query params: `limit`, `offset`, `product_name`, `match_status` (matched/unmatched/manual), `ghl_contact_id`, `date_from`, `date_to`

Response:
```json
{
    "transactions": [
        {
            "id": 1,
            "stripe_payment_id": "pi_3Q...",
            "customer_email": "sarah@email.com",
            "customer_name": "Sarah Mitchell",
            "amount": 159.00,
            "product_name": "Core Pack",
            "ghl_contact_id": "abc123",
            "ghl_name": "Sarah Mitchell",
            "match_method": "email_exact",
            "match_status": "matched",
            "stripe_created_at": "2026-03-15T10:00:00Z"
        }
    ],
    "total": 142,
    "summary": {
        "total_revenue": 18647.00,
        "unique_customers": 87,
        "match_rate": 78.9,
        "avg_order_value": 131.32,
        "products": {
            "Core Pack": {"count": 45, "revenue": 7155.00},
            "Drop-in": {"count": 32, "revenue": 800.00},
            "Starter Pack": {"count": 28, "revenue": 3052.00},
            "Full Journey": {"count": 20, "revenue": 4380.00},
            "Deep Dive": {"count": 12, "revenue": 2388.00},
            "Foundations": {"count": 5, "revenue": 845.00}
        }
    }
}
```

**`GET /api/transactions/ltv`**

Customer LTV leaderboard from the `contact_ltv` table.

Query params: `limit`, `offset`, `sort_by` (net_revenue, transaction_count, last_purchase_at)

Response:
```json
{
    "contacts": [
        {
            "ghl_contact_id": "abc123",
            "ghl_name": "Sarah Mitchell",
            "ghl_email": "sarah@email.com",
            "net_revenue": 547.00,
            "transaction_count": 4,
            "first_purchase_at": "2025-09-20T10:00:00Z",
            "last_purchase_at": "2026-04-15T10:00:00Z",
            "avg_order_value": 136.75,
            "days_as_customer": 207,
            "products_purchased": [
                {"name": "Core Pack", "count": 2, "total": 318.00},
                {"name": "Drop-in", "count": 1, "total": 25.00},
                {"name": "Deep Dive", "count": 1, "total": 199.00}
            ]
        }
    ],
    "total": 87,
    "summary": {
        "median_ltv": 179.00,
        "avg_ltv": 214.33,
        "top_10_pct_ltv": 498.00,
        "total_customer_revenue": 18647.00
    }
}
```

**`POST /api/transactions/{id}/match`**

Manually match an unmatched transaction to a GHL contact. Updates the `stripe_transactions` row, adds to `contact_identity_map` with `confirmed = true`, and recomputes LTV for the contact.

Request body:
```json
{
    "ghl_contact_id": "abc123"
}
```

**`GET /api/transactions/unmatched`**

List transactions with no GHL match. Shows suggested matches from fuzzy name candidates.

---

**`GET /api/conversions`**

List all matched conversions with filters.

Query params: `limit`, `offset`, `status` (sent/failed/pending), `match_method`, `source` (webhook/backfill)

Response:
```json
{
    "conversions": [
        {
            "id": 42,
            "stripe_session_id": "cs_live_...",
            "stripe_email": "sarah@email.com",
            "amount": 159.00,
            "ghl_name": "Sarah Mitchell",
            "match_method": "email_exact",
            "capi_status": "sent",
            "has_fbclid": true,
            "source": "webhook",
            "created_at": "2026-05-04T14:00:00Z"
        }
    ],
    "total": 38,
    "stats": {
        "total_sent": 35,
        "total_failed": 3,
        "match_rate": 80.8,
        "fbclid_rate": 42.1,
        "total_revenue_tracked": 5247.00
    }
}
```

**`GET /api/conversions/{id}`**

Full detail for a single conversion including match candidates and CAPI response.

**`POST /api/conversions/{id}/retry`**

Retry a failed CAPI send.

**`GET /api/conversions/stats`**

Dashboard stats: match rates, CAPI success rates, revenue tracked, fbclid coverage, match method distribution.

**`GET /api/conversions/unmatched`**

List Stripe payments that couldn't be matched to any GHL contact. These need manual review or indicate contacts that exist in Stripe but not in GHL.

**`POST /api/conversions/identity-map/{id}/confirm`**

Manually confirm a fuzzy match. Sets `confirmed = true` on the identity map entry so future purchases from this customer skip matching entirely.

**`DELETE /api/conversions/identity-map/{id}`**

Remove an incorrect match from the identity map (e.g., a false-positive fuzzy match).

---

## Frontend: Conversions tab

Add to the existing React dashboard alongside the Sync and Audit tabs.

### Dashboard view

**Stats bar at top:**
- Total Conversions Tracked | Revenue Tracked | Match Rate | CAPI Success Rate | fbclid Coverage
- Each metric shows current value and trend arrow if historical data exists

**Conversion list table:**
- Columns: Date | Customer | Amount | Match Method | Match Score | fbclid | CAPI Status | Source
- "Match Method" shows color-coded badges: green for email_exact, blue for phone_exact, amber for name_fuzzy, red for none
- "fbclid" shows ✓ (green) or ✗ (gray) — indicates whether Meta can do deterministic attribution
- "CAPI Status" shows sent (green), failed (red with retry button), pending (gray)
- Row click expands to show full match details and CAPI response

**Unmatched section:**
- List of Stripe payments with no GHL match
- Shows Stripe email/name and suggested GHL contacts (from match_candidates)
- "Link to contact" button for manual matching

**Backfill trigger:**
- "Run Backfill" button with days_back input (default 90)
- Progress indicator during backfill
- Results summary when complete

---

## Integration with existing app

### Main app

```python
from routers.conversions import router as conversions_router
app.include_router(conversions_router, prefix="/api/conversions", tags=["conversions"])
```

### Requirements

Add to `requirements.txt`:
```
thefuzz>=0.22.0
python-Levenshtein>=0.25.0
```

### Stripe webhook configuration

In Stripe Dashboard → Developers → Webhooks → Add endpoint:
- URL: `https://your-app-domain/api/webhooks/stripe-conversion`
- Events: `checkout.session.completed`
- This is a separate webhook from the existing sync webhook (they can coexist)

---

## Health context scrubbing checklist

Before any data is sent to Meta CAPI, verify NONE of these are in the payload:

| Field | Status |
|---|---|
| Product name (e.g., "Prenatal Core Pack") | NEVER sent |
| Product category (e.g., "prenatal yoga") | NEVER sent |
| Content type / content name | NEVER sent |
| URL from yogisoul.yoga | NEVER sent |
| URL path (e.g., "/prenatal-pricing") | NEVER sent |
| event_source_url field | OMITTED entirely — no domain referenced in payload |
| Class type (prenatal / postnatal) | NEVER sent |
| Session type (group / private) | NEVER sent |
| Any word: prenatal, postnatal, pregnancy, yoga, trimester, fertility | NEVER sent |

What IS sent:

| Field | Format | Example |
|---|---|---|
| Email | SHA-256 hash | `a1b2c3d4...` |
| Phone | SHA-256 hash | `e5f6a7b8...` |
| First name | SHA-256 hash | `c9d0e1f2...` |
| Last name | SHA-256 hash | `a3b4c5d6...` |
| City, State, Zip | SHA-256 hash | `f7e8d9c0...` |
| fbclid | Raw (Meta's own identifier) | `fb.1.1714...AbC` |
| fbp | Raw (Meta's own identifier) | `fb.1.1714...xyz` |
| Event name | Neutral custom name | `evt_complete` |
| Value | Dollar amount | `159.00` |
| Currency | ISO code | `USD` |
| Event time | Unix timestamp | `1714838400` |

---

## Data flow summary

```
META SETUP (one-time, 10 minutes):
    Events Manager → Connect Data Sources → Web → name it "Conversions"
        → Skip pixel installation (not needed)
        → Dataset ID → META_CAPI_DATASET_ID in .env
        → Settings → Generate Access Token → META_CAPI_ACCESS_TOKEN in .env
        → Send test event via curl → verify in Test Events tab
        → If Meta requires domain verification: register a clean generic domain,
          add DNS TXT record, associate with dataset — no website needed

FULL STRIPE HISTORY SYNC (one-time, then ongoing):
    POST /api/transactions/sync { days_back: null }
        → Pull ALL PaymentIntents (succeeded) from Stripe
        → Pull ALL Charges not linked to a PaymentIntent
        → For each payment:
            → Extract customer email, phone, name, amount, currency
            → Fetch line items / product name from Checkout Session (if exists)
            → Fall back to charge metadata or description for product name
            → Match to GHL contact (identity map → email → phone → fuzzy name)
            → Store full transaction in stripe_transactions (with product details)
            → Cache match in contact_identity_map
        → Recompute LTV for all matched contacts → contact_ltv table
        → Product details stay in YOUR database — never sent to Meta

REAL-TIME CONVERSION TRACKING (going forward):
    Stripe checkout.session.completed webhook
        → /api/webhooks/stripe-conversion
        → Extract email, phone, name, amount (no product info)
        → Check identity_map for cached match
        → If no cache: match against GHL contacts (email → phone → fuzzy name)
        → Extract fbclid + attribution from GHL contact
        → Build clean CAPI event (neutral event name, hashed PII, value only)
        → POST to Meta Conversions API (clean dataset, no domain association)
        → Store in matched_conversions table
        → Also store in stripe_transactions (with product details, locally only)
        → Update contact_ltv for this customer

CAPI BACKFILL (send historical conversions to Meta):
    POST /api/conversions/backfill { days_back: 90 }
        → Read from stripe_transactions (already synced)
        → For each transaction not yet in matched_conversions:
            → Build clean CAPI event (no product info, no health context)
            → POST to Meta Conversions API
            → Store in matched_conversions
        → Events > 7 days old: weaker attribution but still useful
        → Events > 28 days old: sent as offline conversions (lower match rate)

NO PIXEL INVOLVED AT ANY POINT:
    → No JavaScript installed on yogisoul.yoga or any other website
    → No domain URL sent in any CAPI event payload
    → The health-classified yogisoul.yoga domain is never referenced
    → All events go server-to-server: your FastAPI app → Meta CAPI endpoint
    → Product names, class types, health context stay in Postgres only
```

---

## File tree (new files only)

```
backend/
    services/
        conversion_tracker.py    # Stripe extraction, CAPI construction, pipeline orchestration
        identity_resolver.py     # Fuzzy matching, identity map, phone normalization
        transaction_sync.py      # Full Stripe history pull, payment normalization, LTV computation
    routers/
        conversions.py           # CAPI webhook, CAPI backfill, conversion list, manual matching
        transactions.py          # Transaction sync, LTV leaderboard, unmatched list, manual match
    migrations/
        005_stripe_transactions.sql
        006_contact_ltv.sql
        007_matched_conversions.sql
        008_contact_identity_map.sql
```
