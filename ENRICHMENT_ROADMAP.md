# Audit Report Enrichment Roadmap

Data sources that can be added to the intelligence audit to improve AI analysis quality.
Ordered by ROI (impact vs. effort).

---

## Priority 1 — Highest Impact, Low Effort

### Business Profile (manual input)
**Status:** ✅ Implemented

Store per-account context so Claude can make grounded assessments ("your $42 CPA is high for a $97 product") instead of pure pattern-matching on numbers.

Fields: industry, business description, target customer, average order value, primary goal, website URL, Facebook page ID, competitor page IDs.

---

## Priority 2 — High Impact, Low Effort

### Facebook / Instagram Page Stats
**Status:** ✅ Implemented

Already authenticated via the Meta token. Pulls public page data for the account's own Facebook page.

Data: follower count, rating, review count, posting frequency, last post date, about text, page category.

Why it matters: An account spending $10k/month with 200 followers and no posts in 3 weeks is a critical signal the AI should flag.

API: `GET /{page_id}?fields=name,fan_count,followers_count,overall_star_rating,rating_count,about,category,posts`

---

### Meta Ad Library — Own Account + Competitors
**Status:** ✅ Implemented

Public API, no extra auth beyond the existing token. Shows what ads are actively running for any page.

Own account: validates that the ads the account thinks are running actually are, catches zombie ads.
Competitors: shows what proven creatives (long-running ads = winners) competitors are using.

API: `GET /ads_archive?search_page_ids={page_id}&ad_reached_countries=US`

Requires competitor Facebook page IDs in the business profile.

---

## Priority 3 — High Impact, Medium Effort

### Website Scrape
**Status:** ✅ Implemented

Scrapes the business website to extract: page title, meta description, h1/h2 headings, pricing mentions, CTA text, and a content summary. Gives Claude real landing page context — if the ad promises fast delivery but the site copy doesn't mention it, that's a conversion problem.

Implementation: httpx + BeautifulSoup. Runs at audit time if `website_url` is set on the account.

---

### Google Business Profile
**Status:** 🔲 Not yet implemented

**Requires:** `GOOGLE_PLACES_API_KEY` in `.env`

Pulls public business data from Google Maps / Search: star rating, review count, review sentiment, business hours, categories, photos count, Q&A.

Especially valuable for local businesses — a 3.2-star rating with 12 reviews running $5k/month in ads is a conversion problem no amount of ad optimization will fix.

API: `GET https://maps.googleapis.com/maps/api/place/findplacefromtext/json?input={business_name}&inputtype=textquery&fields=...&key={api_key}`

**To implement:**
1. Add `GOOGLE_PLACES_API_KEY` to `config.py` and `.env.example`
2. Create `backend/services/google_places.py` with `fetch_business_profile(name, website_url, api_key)`
3. Add result to `build_audit_payload()` as `payload["business_context"]["google_profile"]`
4. Add business name field to the account business profile form

---

### GHL Pipeline Conversion Rates
**Status:** 🔲 Not yet implemented

Unique data no external tool has. Shows whether Meta leads are converting through the GHL pipeline or dropping at a specific stage.

This closes the loop: Meta says 50 leads generated → GHL shows 8 made it to "Qualified" and 2 closed. That's a 4% close rate — the AI can then assess whether the Meta targeting is attracting the right people.

**To implement:**
1. Query GHL pipeline stages API for contacts tagged as Meta leads
2. Count contacts at each stage
3. Add funnel data to audit payload as `payload["business_context"]["ghl_pipeline"]`

---

## Priority 4 — Medium Impact, Medium Effort

### Review Platform Scrape (Trustpilot / Yelp / G2)
**Status:** 🔲 Not yet implemented

Public scrape of review sentiment, volume, recency, and common complaint themes. If reviews mention "hard to cancel" and the ads promise "no commitment," that's a credibility gap.

---

### Instagram Graph API — Content Mix
**Status:** 🔲 Not yet implemented

Pulls the account's recent Instagram posts: format mix (reels vs. static), caption length, posting cadence, engagement rates. Helps Claude assess whether organic and paid are aligned.

**Requires:** Instagram Basic Display API permission on the token.

---

## Priority 5 — High Impact, High Effort (requires client OAuth)

### Google Search Console
**Status:** 🔲 Not yet implemented

Which organic queries the business ranks for, click-through rates, impressions. Tells Claude if paid and organic are cannibalizing or complementing each other.

**Requires:** Per-client OAuth2 flow with Search Console scope.

---

### Google Ads
**Status:** 🔲 Not yet implemented

Cross-channel spend comparison, search impression share, keyword overlap with Meta. Shows if Meta is the primary channel or part of a mix.

**Requires:** Google Ads API access + per-client OAuth or manager account link.

---

### Klaviyo / Mailchimp Email Metrics
**Status:** 🔲 Not yet implemented

List size, open rates, revenue per email. Shows if the retention funnel can monetize the leads Meta is generating. A high-CPA account with 60% email open rates and strong email revenue looks very different from one with 8% open rates.

**Requires:** Per-client API key or OAuth.

---

## Implementation Notes

- All enrichment data lands in `payload["business_context"]` inside `build_audit_payload()`
- The Claude system prompt references `business_context` fields explicitly
- Business profile is stored in `AdAccount.business_profile` (JSON column) + `AdAccount.website_url`
- New enrichment sources should follow the pattern: async fetch function → result added to payload → prompt references it
