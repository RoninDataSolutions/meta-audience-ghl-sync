"""
Meta Ad Account Audit Engine.

Fetches performance data across multiple time windows, summarizes it,
sends it to AI models for analysis, and persists results to the database.
"""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from config import settings
from models import AuditReport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://graph.facebook.com/v21.0"
MAX_RETRIES = 3
RETRY_DELAYS_429 = [10, 30, 60]

OBJECTIVE_TO_PRIMARY_ACTION = {
    "OUTCOME_LEADS": "lead",
    "LEAD_GENERATION": "lead",
    "OUTCOME_SALES": "purchase",
    "CONVERSIONS": "offsite_conversion.fb_pixel_purchase",
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

GENERIC_ACTIONS = {
    "page_engagement",
    "post",
    "comment",
    "like",
    "photo_view",
    "post_reaction",
}

def _format_contexts(contexts: list[dict]) -> str | None:
    """Format audit_contexts list into a single string for the AI prompt."""
    parts = []
    for c in (contexts or []):
        text = (c.get("text") or "").strip()
        if not text:
            continue
        added_at = (c.get("added_at") or "")[:10]
        parts.append(f"[{added_at}] {text}" if added_at else text)
    return "\n\n".join(parts) or None


INSIGHT_FIELDS = (
    "campaign_name,campaign_id,adset_name,adset_id,ad_name,ad_id,objective,"
    "spend,impressions,reach,clicks,cpc,cpm,ctr,frequency,"
    "actions,action_values,cost_per_action_type"
)

# ---------------------------------------------------------------------------
# AI system prompt
# ---------------------------------------------------------------------------

AUDIT_SYSTEM_PROMPT = """You are a senior paid-media strategist auditing a Meta (Facebook/Instagram) ad account. This account may run any combination of campaign objectives — lead gen, e-commerce/purchase, traffic, awareness, engagement, app installs, video views, or messaging. Analyze whatever is present.

You will receive structured performance data for 7-day, 30-day, 60-day, and 90-day windows at campaign, ad set, and ad levels, plus platform/placement breakdowns, demographic breakdowns, creative metadata, and audience information.

IMPORTANT — Business Context:
The payload may contain a "business_context" key with the following enrichment data. USE THIS to make grounded, specific assessments:
- "profile": manually entered business info — industry, target customer, avg order value, primary goal. Use this to benchmark CPA, ROAS, and CTR against realistic expectations for the business type.
- "business_notes": FREE-TEXT NOTES describing how this business actually works. THIS IS CRITICAL GROUND TRUTH — treat it as authoritative. It may describe the pricing model (e.g. packages vs subscriptions vs one-time), the customer repurchase cycle, retention mechanics, offer structure, seasonality, upsell paths, customer lifetime journey, or nuances not captured in the structured profile fields. If it says customers buy packages and repurchase when they expire, factor this directly into CPA targets (the real LTV is multi-purchase), audience strategy (lapsed customers = high-value retargeting pool), and retention recommendations. If it describes seasonality, weight projections accordingly. Never ignore this field.
- "report_context": NOTES SPECIFIC TO THIS AUDIT PERIOD — what happened, what changed, what was launched. Use this to explain metric anomalies, contextualize this period's performance, and ground period-specific projections. If a flash sale ran, CPA will be abnormally low. If an instructor left, retention metrics may dip. Always reference this context when present.
- "website": scraped from the business website — title, meta description, headings, pricing found, CTAs, platform (Shopify, Kajabi, etc.). Use this to assess landing page/ad message alignment and price-point context.
- "page_stats": the business's own Facebook page — follower count, rating, posting frequency. Flag if the page is thin or inactive relative to ad spend.
- "own_ad_library": their currently active ads in the Meta Ad Library — total count, oldest running ad (long-running = proven winner or fatigue risk). Flag zombie ads.
- "competitor_ads": active ads from competitor pages. Note format, messaging, and creative patterns the competition is using. Identify gaps or opportunities.
- "conversation_insights": patterns extracted from real pre-sale Instagram DM and WhatsApp conversations (last 60 days). Contains top_questions prospects ask, top_objections, conversion_signals that indicate readiness to book, and messaging_gaps where ad messaging doesn't match what prospects actually need to know before converting. This is ground-truth prospect voice data — treat it as highly authoritative. Use it to evaluate whether ad copy addresses real concerns, recommend specific CTA and messaging changes, and identify friction in the pre-sale funnel. If ads promise something prospects never ask about but do ask about pricing or schedule, that's a messaging alignment problem.
- "ltv_insights": customer lifetime value from the CRM. Contains median/avg LTV, distribution (p25/p75), and cohort_trend showing how LTV changes month-over-month for customers acquired in different periods. Since ~100% of customers come from Meta, this IS Meta acquisition quality. Use it to: calibrate CPA targets (if median LTV is $400, a $60 CPA is a 6.7x return — factor this into whether CPA is actually good or bad), flag if recent cohorts show declining LTV even if CPA looks stable (acquiring cheaper but lower-quality customers), and note the trend direction in your projections.

When business_context is present, integrate it throughout your analysis — don't summarize it separately. Cite actual prices, CTAs, page follower counts, competitor observations, and business model specifics inline. The business_notes and report_context fields in particular should visibly shape your campaign recommendations, CPA benchmarks, audience strategy, and projections.

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
    "priority_actions": ["Top 5 ordered actions to take this week, with expected impact for each"],
    "projection_30d": {
        "trajectory": "improving | declining | stable | volatile",
        "summary": "2-3 sentence forward-looking outlook for the next 30 days based on current momentum. Ground this in the 7d-vs-30d direction and the 30d-vs-60d-vs-90d trend slope.",
        "projected_spend": 1500.00,
        "projected_conversions": 45,
        "projected_cpa": 33.33,
        "projected_roas": 2.80,
        "key_drivers": ["The 1-3 factors most influencing this projection — cite specific campaigns or trends"],
        "upside_scenario": "Best case outcome if you execute the top 2-3 recommendations this week",
        "downside_scenario": "Likely outcome if current weak points go unaddressed for 30 days",
        "confidence": "high | medium | low",
        "confidence_note": "Why — thin data, high volatility, recent structural changes, strong trend signal, etc."
    },
    "action_plan": {
        "executive_brief": "1-2 paragraph concrete implementation directive for the next 30 days. Be prescriptive. Reference the business context (industry, primary goal, AOV, target customer) if available.",
        "campaigns_to_create": [
            {
                "priority": 1,
                "name": "Suggested campaign name",
                "objective": "OUTCOME_LEADS | OUTCOME_SALES | OUTCOME_TRAFFIC | ...",
                "audience": "Specific audience — who, how to build it (custom audience, lookalike, interest), sizing estimate",
                "daily_budget": "$X/day",
                "creative_direction": "Specific format, messaging angle, visual concept, and CTA. Reference the business product/service and target customer if known.",
                "expected_result": "~X leads/purchases at $Y CPA based on this account's current benchmarks"
            }
        ],
        "campaigns_to_cut": ["Campaign Name — specific reason it should pause or stop immediately"],
        "audiences_to_build": ["Specific audience with build instructions and why it will improve performance"],
        "budget_moves": ["Reallocate $X/day from [Campaign A] to [Campaign B] — rationale tied to specific performance data"],
        "week_by_week": [
            {"week": "Week 1", "actions": ["Specific action", "Specific action"]},
            {"week": "Week 2", "actions": ["Specific action"]},
            {"week": "Week 3", "actions": ["Specific action"]},
            {"week": "Week 4", "actions": ["Specific action"]}
        ]
    }
}

Note: for projected_spend, projected_conversions, projected_cpa, projected_roas — use null (JSON null) if there is insufficient data to project, not 0. campaigns_to_create should have 1-3 entries maximum, each highly specific to this account's business and gaps.

Be specific — cite campaign names, ad set names, ad names, creative formats, placement names, demographic segments, and actual numbers throughout. Don't hedge. Give clear, actionable direction. If data is thin for any section, say so explicitly and explain what it means for the analysis."""

# ---------------------------------------------------------------------------
# Low-level API helpers
# ---------------------------------------------------------------------------


async def _api_get(
    url: str,
    token: str,
    params: dict | None = None,
    timeout: float = 60.0,
) -> dict:
    """GET request to Meta Graph API with retry on 429. Raises on other errors."""
    request_params = dict(params or {})
    request_params["access_token"] = token

    for attempt in range(MAX_RETRIES):
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=request_params)

            if resp.status_code == 429:
                delay = RETRY_DELAYS_429[min(attempt, len(RETRY_DELAYS_429) - 1)]
                logger.warning(
                    f"Meta rate limited (429), retrying in {delay}s (attempt {attempt + 1}/{MAX_RETRIES})"
                )
                await asyncio.sleep(delay)
                continue

            # Check for expired token in JSON body before raising HTTP error
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                    error = body.get("error", {})
                    if error.get("code") == 190:
                        raise ValueError(
                            f"Meta access token is expired or invalid (error code 190). "
                            f"Please regenerate your META_ACCESS_TOKEN. Details: {error.get('message', '')}"
                        )
                except (json.JSONDecodeError, AttributeError):
                    pass
                resp.raise_for_status()

            return resp.json()

    raise RuntimeError(f"Meta API: max retries ({MAX_RETRIES}) exceeded for {url}")


async def _api_get_paginated(
    url: str,
    token: str,
    params: dict | None = None,
) -> list[dict]:
    """GET all pages following paging.next until exhausted."""
    all_items: list[dict] = []
    current_url = url
    current_params = dict(params or {})

    while True:
        data = await _api_get(current_url, token, params=current_params)
        items = data.get("data", [])
        all_items.extend(items)

        paging = data.get("paging", {})
        next_url = paging.get("next")
        if not next_url:
            break

        # The next URL from Meta includes access_token and all params already
        current_url = next_url
        current_params = {}  # params are embedded in the next URL

    return all_items


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _window_dates(days: int) -> tuple[str, str]:
    """Return (since, until) in YYYY-MM-DD for a trailing N-day window.

    until = yesterday (Meta insights are available through yesterday).
    since = until - days + 1.
    """
    until = date.today() - timedelta(days=1)
    since = until - timedelta(days=days - 1)
    return since.strftime("%Y-%m-%d"), until.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


async def fetch_account_info(account_id: str, token: str) -> dict:
    """GET account info: name, account_id, currency, timezone_name, account_status."""
    return await _api_get(
        f"{BASE_URL}/{account_id}",
        token,
        params={"fields": "name,account_id,currency,timezone_name,account_status"},
    )


async def fetch_insights(
    account_id: str,
    token: str,
    level: str,
    since: str,
    until: str,
    time_increment: str,
) -> list[dict]:
    """Fetch all insight rows for a given level/window.

    Filters rows where impressions > 0. Paginates until exhausted.
    level: 'campaign' | 'adset' | 'ad'
    time_increment: '1' (daily) or 'all_days'
    """
    rows = await _api_get_paginated(
        f"{BASE_URL}/{account_id}/insights",
        token,
        params={
            "level": level,
            "fields": INSIGHT_FIELDS,
            "time_range": json.dumps({"since": since, "until": until}),
            "time_increment": time_increment,
            "limit": 500,
        },
    )
    return [r for r in rows if int(r.get("impressions", 0)) > 0]


async def fetch_creative_metadata(ad_ids: list[str], token: str) -> dict[str, dict]:
    """Fetch creative metadata for up to N ads using Meta Batch API (50 per batch).

    Returns dict mapping ad_id -> {format, headline, body, call_to_action, thumbnail_url, link_url}.
    If a fetch fails for an ad, maps it to None.
    """
    if not ad_ids:
        return {}

    creative_fields = (
        "creative{id,thumbnail_url,body,title,call_to_action_type,"
        "object_type,image_url,link_url}"
    )

    result: dict[str, dict | None] = {}
    batch_size = 50

    for i in range(0, len(ad_ids), batch_size):
        batch_ids = ad_ids[i : i + batch_size]
        batch_items = [
            {
                "method": "GET",
                "relative_url": f"v21.0/{ad_id}?fields={creative_fields}",
            }
            for ad_id in batch_ids
        ]

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://graph.facebook.com/",
                    params={"access_token": token},
                    json={"batch": batch_items},
                )
                resp.raise_for_status()
                batch_results = resp.json()
        except Exception as e:
            logger.warning(f"Creative metadata batch failed for batch starting at index {i}: {e}")
            for ad_id in batch_ids:
                result[ad_id] = None
            if i + batch_size < len(ad_ids):
                await asyncio.sleep(1)
            continue

        for ad_id, item in zip(batch_ids, batch_results):
            try:
                if not isinstance(item, dict) or item.get("code") != 200:
                    logger.warning(
                        f"Creative fetch failed for ad {ad_id}: code={item.get('code') if isinstance(item, dict) else 'N/A'}"
                    )
                    result[ad_id] = None
                    continue

                body = json.loads(item["body"])
                creative = body.get("creative", {})
                object_type = creative.get("object_type", "")

                fmt_map = {
                    "VIDEO": "VIDEO",
                    "PHOTO": "PHOTO",
                    "SHARE": "LINK",
                    "STATUS": "STATUS",
                    "OFFER": "OFFER",
                    "EVENT": "EVENT",
                }
                fmt = fmt_map.get(object_type, object_type or "UNKNOWN")

                result[ad_id] = {
                    "format": fmt,
                    "headline": creative.get("title", ""),
                    "body": creative.get("body", ""),
                    "call_to_action": creative.get("call_to_action_type", ""),
                    "thumbnail_url": creative.get("thumbnail_url") or creative.get("image_url", ""),
                    "link_url": creative.get("link_url", ""),
                }
            except Exception as e:
                logger.warning(f"Failed to parse creative for ad {ad_id}: {e}")
                result[ad_id] = None

        if i + batch_size < len(ad_ids):
            await asyncio.sleep(1)

    return result


async def fetch_audiences(account_id: str, token: str) -> list[dict]:
    """GET custom audiences for an ad account."""
    return await _api_get_paginated(
        f"{BASE_URL}/{account_id}/customaudiences",
        token,
        params={
            "fields": (
                "name,id,approximate_count_lower_bound,approximate_count_upper_bound,"
                "subtype,time_updated,delivery_status"
            ),
            "limit": 200,
        },
    )


async def fetch_breakdown(
    account_id: str,
    token: str,
    breakdowns: str,
    since: str,
    until: str,
) -> list[dict]:
    """Fetch breakdown insights (adset level, all_days, 30d window)."""
    return await _api_get_paginated(
        f"{BASE_URL}/{account_id}/insights",
        token,
        params={
            "level": "adset",
            "fields": "adset_name,adset_id,spend,impressions,clicks,ctr,cpc,cpm,actions,action_values",
            "time_range": json.dumps({"since": since, "until": until}),
            "time_increment": "all_days",
            "breakdowns": breakdowns,
            "limit": 500,
        },
    )


# ---------------------------------------------------------------------------
# Enrichment fetchers
# ---------------------------------------------------------------------------

async def fetch_page_stats(page_id: str, token: str) -> dict:
    """Fetch public Facebook page stats — follower count, rating, recent activity."""
    if not page_id:
        return {}
    try:
        data = await _api_get(
            f"{BASE_URL}/{page_id}",
            token,
            params={
                "fields": (
                    "name,fan_count,followers_count,overall_star_rating,"
                    "rating_count,about,category,website,posts.limit(5){message,created_time}"
                ),
            },
        )
        posts = data.pop("posts", {}).get("data", [])
        last_post_date = posts[0].get("created_time", "") if posts else None

        # Derive posting frequency from last 5 posts
        post_dates = []
        for p in posts:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(p["created_time"].replace("Z", "+00:00"))
                post_dates.append(dt)
            except Exception:
                pass

        posts_per_week = None
        if len(post_dates) >= 2:
            span_days = (post_dates[0] - post_dates[-1]).days or 1
            posts_per_week = round(len(post_dates) / span_days * 7, 1)

        return {
            "page_id": page_id,
            "name": data.get("name"),
            "fan_count": data.get("fan_count"),
            "followers_count": data.get("followers_count"),
            "rating": data.get("overall_star_rating"),
            "review_count": data.get("rating_count"),
            "about": data.get("about", "")[:300],
            "category": data.get("category"),
            "website": data.get("website"),
            "last_post_date": last_post_date,
            "posts_per_week_est": posts_per_week,
        }
    except Exception as e:
        logger.warning(f"fetch_page_stats failed for page {page_id}: {e}")
        return {"page_id": page_id, "error": str(e)[:200]}


async def fetch_ad_library(page_id: str, token: str, country: str = "US") -> dict:
    """
    Fetch active ads from the Meta Ad Library for a given page.
    Returns own active ads + summary stats.
    """
    if not page_id:
        return {}
    try:
        rows = await _api_get_paginated(
            f"{BASE_URL}/ads_archive",
            token,
            params={
                "search_page_ids": page_id,
                "ad_reached_countries": country,
                "ad_active_status": "ACTIVE",
                "fields": (
                    "id,ad_creative_body,ad_creative_link_title,"
                    "ad_creative_link_description,ad_delivery_start_time,"
                    "ad_snapshot_url,page_name,impressions,spend"
                ),
                "limit": 50,
            },
        )
        # Summarise
        total_active = len(rows)
        oldest_start = None
        for r in rows:
            start = r.get("ad_delivery_start_time", "")
            if start and (oldest_start is None or start < oldest_start):
                oldest_start = start

        sample = []
        for r in rows[:5]:
            sample.append({
                "title": (r.get("ad_creative_link_title") or "")[:80],
                "body": (r.get("ad_creative_body") or "")[:150],
                "running_since": r.get("ad_delivery_start_time"),
                "snapshot_url": r.get("ad_snapshot_url"),
            })

        return {
            "page_id": page_id,
            "total_active_ads": total_active,
            "oldest_active_since": oldest_start,
            "sample_ads": sample,
        }
    except Exception as e:
        logger.warning(f"fetch_ad_library failed for page {page_id}: {e}")
        return {"page_id": page_id, "error": str(e)[:200]}


async def fetch_competitor_ads(competitor_page_ids: list[str], token: str, country: str = "US") -> list[dict]:
    """Fetch active ads from competitor pages via the Ad Library."""
    if not competitor_page_ids:
        return []
    results = []
    for page_id in competitor_page_ids[:5]:  # cap at 5 competitors
        try:
            rows = await _api_get_paginated(
                f"{BASE_URL}/ads_archive",
                token,
                params={
                    "search_page_ids": page_id,
                    "ad_reached_countries": country,
                    "ad_active_status": "ACTIVE",
                    "fields": (
                        "id,ad_creative_body,ad_creative_link_title,"
                        "ad_delivery_start_time,page_name"
                    ),
                    "limit": 20,
                },
            )
            if rows:
                results.append({
                    "page_id": page_id,
                    "page_name": rows[0].get("page_name", page_id),
                    "active_ad_count": len(rows),
                    "sample_creatives": [
                        {
                            "title": (r.get("ad_creative_link_title") or "")[:80],
                            "body": (r.get("ad_creative_body") or "")[:150],
                            "running_since": r.get("ad_delivery_start_time"),
                        }
                        for r in rows[:3]
                    ],
                })
        except Exception as e:
            logger.warning(f"fetch_competitor_ads failed for {page_id}: {e}")
    return results


# ---------------------------------------------------------------------------
# Summarization helpers
# ---------------------------------------------------------------------------


def extract_all_actions(row: dict) -> dict:
    """Extract all action types from a single insight row.

    Returns {action_type: {count, value, cost}}.
    """
    actions_list = row.get("actions") or []
    values_list = row.get("action_values") or []
    cost_list = row.get("cost_per_action_type") or []

    actions_map: dict[str, dict] = {}
    for item in actions_list:
        atype = item.get("action_type", "")
        if atype:
            actions_map.setdefault(atype, {"count": 0.0, "value": 0.0, "cost": 0.0})
            actions_map[atype]["count"] += float(item.get("value", 0))

    for item in values_list:
        atype = item.get("action_type", "")
        if atype and atype in actions_map:
            actions_map[atype]["value"] += float(item.get("value", 0))

    for item in cost_list:
        atype = item.get("action_type", "")
        if atype and atype in actions_map:
            actions_map[atype]["cost"] = float(item.get("value", 0))

    return actions_map


def get_primary_action(objective: str, all_actions: dict) -> str:
    """Determine primary action from objective.

    Falls back to highest-count non-generic action if objective mapping not found
    or the mapped action has 0 count.
    """
    mapped = OBJECTIVE_TO_PRIMARY_ACTION.get(objective or "", "")
    if mapped and all_actions.get(mapped, {}).get("count", 0) > 0:
        return mapped

    # Fall back to highest-count non-generic action
    best_action = ""
    best_count = 0.0
    for atype, metrics in all_actions.items():
        if atype in GENERIC_ACTIONS:
            continue
        if metrics.get("count", 0) > best_count:
            best_count = metrics["count"]
            best_action = atype

    return best_action or mapped or "link_click"


def _merge_actions(base: dict, addition: dict) -> dict:
    """Merge two all_actions dicts by summing counts and values."""
    merged = {k: dict(v) for k, v in base.items()}
    for atype, metrics in addition.items():
        if atype in merged:
            merged[atype]["count"] = merged[atype].get("count", 0) + metrics.get("count", 0)
            merged[atype]["value"] = merged[atype].get("value", 0) + metrics.get("value", 0)
            # cost will be recomputed from totals later
        else:
            merged[atype] = dict(metrics)
    return merged


def summarize_entities(
    rows: list[dict],
    id_field: str,
    name_field: str,
    daily: bool,
    parent_fields: list[str] | None = None,
) -> list[dict]:
    """Group rows by entity ID, aggregate metrics, compute derived metrics.

    Returns list of entity summary dicts sorted by spend descending.
    """
    if parent_fields is None:
        parent_fields = []

    grouped: dict[str, dict] = {}

    for row in rows:
        entity_id = row.get(id_field, "")
        entity_name = row.get(name_field, "")
        if not entity_id:
            continue

        if entity_id not in grouped:
            entry: dict[str, Any] = {
                "id": entity_id,
                "name": entity_name,
                "objective": row.get("objective", ""),
                "spend": 0.0,
                "impressions": 0,
                "reach": 0,
                "clicks": 0,
                "all_actions": {},
                "_daily_rows": [],
            }
            for pf in parent_fields:
                entry[pf] = row.get(pf, "")
            grouped[entity_id] = entry

        entry = grouped[entity_id]
        entry["spend"] += float(row.get("spend", 0))
        entry["impressions"] += int(row.get("impressions", 0))
        entry["reach"] += int(row.get("reach", 0))
        entry["clicks"] += int(row.get("clicks", 0))
        entry["all_actions"] = _merge_actions(entry["all_actions"], extract_all_actions(row))

        if daily:
            entry["_daily_rows"].append(
                {
                    "date": row.get("date_start", ""),
                    "impressions": int(row.get("impressions", 0)),
                    "reach": int(row.get("reach", 0)),
                    "frequency": float(row.get("frequency", 0)),
                }
            )

    summaries = []
    for entry in grouped.values():
        spend = entry["spend"]
        impressions = entry["impressions"]
        reach = max(entry["reach"], 1)
        clicks = entry["clicks"]
        all_actions = entry["all_actions"]

        # Recompute costs from aggregated totals
        for atype, metrics in all_actions.items():
            count = metrics.get("count", 0)
            metrics["cost"] = round(spend / count, 4) if count > 0 else 0.0

        cpm = round(spend / impressions * 1000, 4) if impressions > 0 else 0.0
        cpc = round(spend / clicks, 4) if clicks > 0 else 0.0
        ctr = round(clicks / impressions * 100, 4) if impressions > 0 else 0.0
        frequency = round(impressions / reach, 4)

        primary_action = get_primary_action(entry.get("objective", ""), all_actions)
        pa_metrics = all_actions.get(primary_action, {"count": 0.0, "value": 0.0, "cost": 0.0})
        primary_action_count = pa_metrics.get("count", 0.0)
        primary_action_value = pa_metrics.get("value", 0.0)
        primary_action_cost = round(spend / primary_action_count, 4) if primary_action_count > 0 else 0.0
        roas = round(primary_action_value / spend, 4) if spend > 0 else 0.0

        days_active = len(entry["_daily_rows"]) if daily else None

        summary: dict[str, Any] = {
            "id": entry["id"],
            "name": entry["name"],
            "objective": entry.get("objective", ""),
            "spend": round(spend, 2),
            "impressions": impressions,
            "reach": entry["reach"],
            "clicks": clicks,
            "cpm": cpm,
            "cpc": cpc,
            "ctr": ctr,
            "frequency": frequency,
            "all_actions": all_actions,
            "primary_action": primary_action,
            "primary_action_count": primary_action_count,
            "primary_action_value": round(primary_action_value, 2),
            "primary_action_cost": primary_action_cost,
            "roas": roas,
        }

        if days_active is not None:
            summary["days_active"] = days_active

        # Frequency trend computation for daily rows
        if daily:
            daily_rows = sorted(entry["_daily_rows"], key=lambda r: r["date"])
            last7 = daily_rows[-7:]
            if last7:
                imp7 = sum(r["impressions"] for r in last7)
                reach7 = max(sum(r["reach"] for r in last7), 1)
                frequency_7d = round(imp7 / reach7, 4)
            else:
                frequency_7d = 0.0
            summary["frequency_7d"] = frequency_7d
            summary["frequency_trend"] = round(frequency_7d - frequency, 4)

        for pf in parent_fields:
            summary[pf] = entry.get(pf, "")

        summaries.append(summary)

    summaries.sort(key=lambda x: x["spend"], reverse=True)
    return summaries


def summarize_placement_breakdown(rows: list[dict], total_spend: float) -> list[dict]:
    """Group by publisher_platform + platform_position. Sort by spend desc."""
    grouped: dict[str, dict] = {}
    for row in rows:
        platform = row.get("publisher_platform", "unknown")
        position = row.get("platform_position", "unknown")
        key = f"{platform}|{position}"
        if key not in grouped:
            grouped[key] = {
                "publisher_platform": platform,
                "platform_position": position,
                "spend": 0.0,
                "impressions": 0,
                "clicks": 0,
                "all_actions": {},
            }
        g = grouped[key]
        g["spend"] += float(row.get("spend", 0))
        g["impressions"] += int(row.get("impressions", 0))
        g["clicks"] += int(row.get("clicks", 0))
        g["all_actions"] = _merge_actions(g["all_actions"], extract_all_actions(row))

    results = []
    for g in grouped.values():
        spend = g["spend"]
        impressions = g["impressions"]
        clicks = g["clicks"]
        results.append(
            {
                "publisher_platform": g["publisher_platform"],
                "platform_position": g["platform_position"],
                "spend": round(spend, 2),
                "impressions": impressions,
                "clicks": clicks,
                "ctr": round(clicks / impressions * 100, 4) if impressions > 0 else 0.0,
                "cpc": round(spend / clicks, 4) if clicks > 0 else 0.0,
                "cpm": round(spend / impressions * 1000, 4) if impressions > 0 else 0.0,
                "all_actions": g["all_actions"],
                "pct_of_total_spend": round(spend / total_spend * 100, 2) if total_spend > 0 else 0.0,
            }
        )

    results.sort(key=lambda x: x["spend"], reverse=True)
    return results


def summarize_demographic_breakdown(rows: list[dict], total_spend: float) -> list[dict]:
    """Group by age + gender. Sort by spend desc."""
    grouped: dict[str, dict] = {}
    for row in rows:
        age = row.get("age", "unknown")
        gender = row.get("gender", "unknown")
        key = f"{age}|{gender}"
        if key not in grouped:
            grouped[key] = {
                "age": age,
                "gender": gender,
                "spend": 0.0,
                "impressions": 0,
                "clicks": 0,
                "all_actions": {},
            }
        g = grouped[key]
        g["spend"] += float(row.get("spend", 0))
        g["impressions"] += int(row.get("impressions", 0))
        g["clicks"] += int(row.get("clicks", 0))
        g["all_actions"] = _merge_actions(g["all_actions"], extract_all_actions(row))

    results = []
    for g in grouped.values():
        spend = g["spend"]
        impressions = g["impressions"]
        clicks = g["clicks"]
        results.append(
            {
                "age": g["age"],
                "gender": g["gender"],
                "spend": round(spend, 2),
                "impressions": impressions,
                "clicks": clicks,
                "ctr": round(clicks / impressions * 100, 4) if impressions > 0 else 0.0,
                "cpc": round(spend / clicks, 4) if clicks > 0 else 0.0,
                "cpm": round(spend / impressions * 1000, 4) if impressions > 0 else 0.0,
                "all_actions": g["all_actions"],
                "pct_of_total_spend": round(spend / total_spend * 100, 2) if total_spend > 0 else 0.0,
            }
        )

    results.sort(key=lambda x: x["spend"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# AI analysis
# ---------------------------------------------------------------------------


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` fences from AI response."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Strip opening fence (```json or ```)
        lines = lines[1:]
        # Strip closing fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def analyze_with_claude(payload_str: str, api_key: str) -> dict:
    """POST to Anthropic API (claude-sonnet-4-20250514). Returns parsed JSON or {"error": ...}."""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 12000,
                    "system": AUDIT_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": payload_str}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["content"][0]["text"]
            cleaned = _strip_markdown_fences(raw_text)
            return json.loads(cleaned)
    except Exception as e:
        logger.error(f"Claude analysis failed: {e}")
        return {"error": str(e)}


async def analyze_with_openai(payload_str: str, api_key: str) -> dict:
    """POST to OpenAI API (gpt-4o). Returns parsed JSON or {"error": ...}."""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "content-type": "application/json",
                },
                json={
                    "model": "gpt-4o",
                    "max_tokens": 12000,
                    "messages": [
                        {"role": "system", "content": AUDIT_SYSTEM_PROMPT},
                        {"role": "user", "content": payload_str},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["choices"][0]["message"]["content"]
            cleaned = _strip_markdown_fences(raw_text)
            return json.loads(cleaned)
    except Exception as e:
        logger.error(f"OpenAI analysis failed: {e}")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Payload truncation
# ---------------------------------------------------------------------------


def _truncate_payload(payload: dict) -> dict:
    """If JSON string of payload > 120,000 chars, truncate in priority order."""
    truncation_notes: list[str] = []

    def _size() -> int:
        return len(json.dumps(payload))

    # 1. Remove 90d ad-level data
    if _size() > 120_000:
        if "windows" in payload and "90d" in payload["windows"]:
            payload["windows"]["90d"].pop("ads", None)
            truncation_notes.append("90d ad-level data removed")

    # 2. Remove 60d ad-level data
    if _size() > 120_000:
        if "windows" in payload and "60d" in payload["windows"]:
            payload["windows"]["60d"].pop("ads", None)
            truncation_notes.append("60d ad-level data removed")

    # 3. Remove 90d adset-level data
    if _size() > 120_000:
        if "windows" in payload and "90d" in payload["windows"]:
            payload["windows"]["90d"].pop("adsets", None)
            truncation_notes.append("90d adset-level data removed")

    # 4. Truncate all_actions to top 5 by count per entity in all windows
    if _size() > 120_000:
        for window_key, window_data in payload.get("windows", {}).items():
            for level_key in ("campaigns", "adsets", "ads"):
                entities = window_data.get(level_key, [])
                for entity in entities:
                    all_actions = entity.get("all_actions", {})
                    if len(all_actions) > 5:
                        top5 = sorted(
                            all_actions.items(),
                            key=lambda kv: kv[1].get("count", 0),
                            reverse=True,
                        )[:5]
                        entity["all_actions"] = dict(top5)
        truncation_notes.append("all_actions truncated to top 5 per entity in all windows")

    # 5. Remove creative.body and creative.headline
    if _size() > 120_000:
        for window_key, window_data in payload.get("windows", {}).items():
            for entity in window_data.get("ads", []):
                creative = entity.get("creative")
                if isinstance(creative, dict):
                    creative.pop("body", None)
                    creative.pop("headline", None)
        truncation_notes.append("creative.body and creative.headline removed")

    if truncation_notes:
        payload["_truncation_note"] = truncation_notes

    return payload


# ---------------------------------------------------------------------------
# GHL enrichment — conversations + LTV
# ---------------------------------------------------------------------------


async def fetch_conversation_insights(days: int = 60) -> dict:
    """
    Pull recent pre-sale IG/WhatsApp/Facebook conversations from GHL,
    anonymize them, then summarize patterns via a lightweight Claude Haiku call.
    Returns structured insights with no PII.
    """
    if not settings.GHL_API_KEY or not settings.GHL_LOCATION_ID:
        return {"error": "GHL not configured"}
    if not settings.CLAUDE_API_KEY:
        return {"error": "Claude API key required for summarization"}

    from api.ghl_client import fetch_conversations, fetch_conversation_messages

    conversations = await fetch_conversations(days=days, max_count=120)
    if not conversations:
        return {"error": "No recent pre-sale conversations found", "sample_size": 0}

    channel_counts: dict[str, int] = {}
    for c in conversations:
        ch = c.get("lastMessageType", "unknown").replace("TYPE_", "").lower()
        channel_counts[ch] = channel_counts.get(ch, 0) + 1

    # Fetch messages concurrently in batches of 15
    async def _safe_messages(conv: dict) -> list[dict]:
        try:
            return await fetch_conversation_messages(conv["id"], limit=20)
        except Exception:
            return []

    all_threads: list[tuple[dict, list[dict]]] = []
    batch_size = 15
    for i in range(0, len(conversations), batch_size):
        batch = conversations[i:i + batch_size]
        results = await asyncio.gather(*[_safe_messages(c) for c in batch])
        for conv, msgs in zip(batch, results):
            if msgs:
                all_threads.append((conv, msgs))
        if i + batch_size < len(conversations):
            await asyncio.sleep(0.3)

    # Build anonymized corpus — strip names, keep message content only
    corpus_parts: list[str] = []
    for conv, msgs in all_threads[:70]:
        inbound = [m for m in msgs if m.get("direction") == "inbound" and (m.get("body") or "").strip()]
        if not inbound:
            continue
        channel = conv.get("lastMessageType", "").replace("TYPE_", "").lower()
        lines = [f"[{channel}]"]
        for m in msgs:
            direction = "prospect" if m.get("direction") == "inbound" else "business"
            body = (m.get("body") or "").strip()
            if body and len(body) < 600:
                lines.append(f"{direction}: {body}")
        if len(lines) > 2:
            corpus_parts.append("\n".join(lines))

    if not corpus_parts:
        return {"error": "No usable conversation content", "sample_size": 0}

    summary_prompt = (
        f"You are analyzing {len(corpus_parts)} pre-sale conversations from a yoga studio's "
        f"Instagram DM and WhatsApp inbox (last {days} days). "
        "These are real conversations between prospects and the business. "
        "Extract patterns only — do not reference specific names or personal details.\n\n"
        "Conversations:\n"
        + "\n\n---\n\n".join(corpus_parts[:60])[:14000]
        + "\n\nReturn ONLY a JSON object with these fields:\n"
        '{"top_questions": [5-8 most common questions prospects ask],'
        '"top_objections": [3-6 objections or hesitations],'
        '"conversion_signals": [3-6 phrases/patterns that indicate a prospect is ready to book],'
        '"messaging_gaps": [2-4 things prospects are confused about that ads should clarify upfront],'
        '"avg_messages_before_booking": estimated number or null,'
        '"channel_notes": one sentence on how IG vs WhatsApp usage differs,'
        '"overall_sentiment": one sentence on general prospect tone and readiness}'
    )

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.CLAUDE_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": summary_prompt}],
                },
            )
            resp.raise_for_status()
            text = resp.json()["content"][0]["text"].strip()
            if "```" in text:
                text = text.split("```")[1].lstrip("json").strip()
            insights = json.loads(text)
            insights["sample_size"] = len(corpus_parts)
            insights["channel_breakdown"] = channel_counts
            insights["date_range_days"] = days
            return insights
    except Exception as e:
        logger.error(f"Conversation insights summarization failed: {e}")
        return {
            "error": str(e)[:200],
            "sample_size": len(corpus_parts),
            "channel_breakdown": channel_counts,
        }


async def fetch_ltv_insights(days_back: int = 180) -> dict:
    """
    Pull contacts with LTV populated from GHL and compute distribution + cohort trends.
    Since ~100% of contacts come from Meta, this is effectively Meta customer LTV.
    """
    if not settings.GHL_API_KEY or not settings.GHL_LOCATION_ID:
        return {"error": "GHL not configured"}

    from api.ghl_client import get_custom_fields, get_all_contacts
    from datetime import timezone, timedelta
    from collections import defaultdict
    import statistics

    # Find LTV field UUID
    try:
        fields = await get_custom_fields()
        ltv_field = next((f for f in fields if f.get("fieldKey") == "contact.ltv"), None)
        if not ltv_field:
            return {"error": "LTV custom field (contact.ltv) not found in GHL"}
        ltv_field_id = ltv_field["id"]
    except Exception as e:
        return {"error": f"Could not fetch custom fields: {e}"}

    try:
        contacts = await get_all_contacts()
    except Exception as e:
        return {"error": f"Could not fetch contacts: {e}"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    ltv_data: list[tuple[datetime, float]] = []

    for c in contacts:
        date_str = c.get("dateAdded") or c.get("createdAt") or ""
        try:
            created = datetime.fromisoformat(date_str.replace("Z", "+00:00")) if date_str else None
        except Exception:
            created = None

        if created and created < cutoff:
            continue

        for f in c.get("customFields", []):
            if f.get("id") == ltv_field_id:
                try:
                    val = float(str(f.get("value") or "0").replace(",", "").replace("$", ""))
                    if val > 0 and created:
                        ltv_data.append((created, val))
                except Exception:
                    pass
                break

    if not ltv_data:
        return {"sample_size": 0, "note": "No contacts with LTV data in the analysis window"}

    values = sorted(v for _, v in ltv_data)
    n = len(values)
    median_ltv = statistics.median(values)
    avg_ltv = statistics.mean(values)
    p25 = values[max(0, n // 4)]
    p75 = values[min(n - 1, (3 * n) // 4)]

    # Monthly cohorts
    cohorts: dict[str, list[float]] = defaultdict(list)
    for created, val in ltv_data:
        cohorts[created.strftime("%Y-%m")].append(val)

    cohort_trend = []
    for month in sorted(cohorts.keys()):
        vals = sorted(cohorts[month])
        cohort_trend.append({
            "month": month,
            "count": len(vals),
            "median_ltv": round(statistics.median(vals), 2),
            "avg_ltv": round(statistics.mean(vals), 2),
        })

    trend = "insufficient_data"
    if len(cohort_trend) >= 3:
        recent = [c["median_ltv"] for c in cohort_trend[-3:]]
        if recent[-1] > recent[0] * 1.1:
            trend = "improving"
        elif recent[-1] < recent[0] * 0.9:
            trend = "declining"
        else:
            trend = "stable"

    return {
        "sample_size": n,
        "median_ltv": round(median_ltv, 2),
        "avg_ltv": round(avg_ltv, 2),
        "ltv_p25": round(p25, 2),
        "ltv_p75": round(p75, 2),
        "max_ltv": round(max(values), 2),
        "cohort_trend": cohort_trend,
        "trend_direction": trend,
        "analysis_period_days": days_back,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def build_audit_payload(
    account_id: str,
    token: str,
    business_profile: dict | None = None,
    website_url: str | None = None,
    business_notes: str | None = None,
    report_notes: str | None = None,
) -> dict:
    """Fetch all Meta data and return the structured audit payload."""

    # Pre-compute date windows
    windows_config = {
        "7d": _window_dates(7),
        "30d": _window_dates(30),
        "60d": _window_dates(60),
        "90d": _window_dates(90),
    }
    since_30d, until_30d = windows_config["30d"]

    # Build parallel task list
    tasks: list[Any] = []
    task_labels: list[str] = []

    # Account info
    tasks.append(fetch_account_info(account_id, token))
    task_labels.append("account_info")

    # 12 insight fetch calls: 4 windows × 3 levels
    # time_increment rules per spec:
    #   campaign: daily (1) for all windows — needed for frequency trending
    #   adset:    daily for 7d/30d, all_days for 60d/90d
    #   ad:       all_days for all windows — creative analysis doesn't need daily
    for window_key, (since, until) in windows_config.items():
        for level in ("campaign", "adset", "ad"):
            if level == "campaign":
                time_increment = "1"
            elif level == "adset":
                time_increment = "1" if window_key in ("7d", "30d") else "all_days"
            else:  # ad
                time_increment = "all_days"
            tasks.append(fetch_insights(account_id, token, level, since, until, time_increment))
            task_labels.append(f"insights_{window_key}_{level}")

    # Audiences
    tasks.append(fetch_audiences(account_id, token))
    task_labels.append("audiences")

    # Placement breakdown (30d)
    tasks.append(
        fetch_breakdown(account_id, token, "publisher_platform,platform_position", since_30d, until_30d)
    )
    task_labels.append("breakdown_placement")

    # Demographic breakdown (30d)
    tasks.append(fetch_breakdown(account_id, token, "age,gender", since_30d, until_30d))
    task_labels.append("breakdown_demographic")

    # Run all in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Unpack results
    result_map: dict[str, Any] = {}
    for label, result in zip(task_labels, results):
        if isinstance(result, Exception):
            logger.warning(f"Task '{label}' failed: {result}")
            result_map[label] = [] if label != "account_info" else {}
        else:
            result_map[label] = result

    account_info = result_map["account_info"]

    # Organize insight rows into a nested dict
    raw_insights: dict[str, dict[str, list[dict]]] = {}
    for window_key in windows_config:
        raw_insights[window_key] = {}
        for level in ("campaign", "adset", "ad"):
            raw_insights[window_key][level] = result_map.get(f"insights_{window_key}_{level}", [])

    audiences = result_map.get("audiences", [])
    placement_rows = result_map.get("breakdown_placement", [])
    demographic_rows = result_map.get("breakdown_demographic", [])

    # Collect unique ad IDs from 30d ad insights and fetch creative metadata
    ad_rows_30d = raw_insights["30d"]["ad"]
    unique_ad_ids = list({row["ad_id"] for row in ad_rows_30d if row.get("ad_id")})
    creative_meta = await fetch_creative_metadata(unique_ad_ids, token)

    # Summarize all windows
    summarized_windows: dict[str, dict] = {}
    for window_key in windows_config:
        # campaign is always daily; adset daily only for 7d/30d; ad never daily
        campaigns = summarize_entities(
            raw_insights[window_key]["campaign"],
            id_field="campaign_id",
            name_field="campaign_name",
            daily=True,
        )
        adsets = summarize_entities(
            raw_insights[window_key]["adset"],
            id_field="adset_id",
            name_field="adset_name",
            daily=window_key in ("7d", "30d"),
            parent_fields=["campaign_name", "campaign_id"],
        )
        ads = summarize_entities(
            raw_insights[window_key]["ad"],
            id_field="ad_id",
            name_field="ad_name",
            daily=False,
            parent_fields=["campaign_name", "campaign_id", "adset_name", "adset_id"],
        )

        # Merge creative metadata into 30d ad summaries
        if window_key == "30d":
            for ad in ads:
                ad_id = ad.get("id", "")
                creative = creative_meta.get(ad_id)
                if creative:
                    ad["creative"] = creative

        summarized_windows[window_key] = {
            "campaigns": campaigns,
            "adsets": adsets,
            "ads": ads,
        }

    # Compute breakdowns
    total_spend_30d = sum(c["spend"] for c in summarized_windows["30d"]["campaigns"])
    placement_breakdown = summarize_placement_breakdown(placement_rows, total_spend_30d)
    demographic_breakdown = summarize_demographic_breakdown(demographic_rows, total_spend_30d)

    payload = {
        "account": account_info,
        "windows": summarized_windows,
        "audiences": audiences,
        "breakdowns_30d": {
            "by_placement": placement_breakdown,
            "by_demographic": demographic_breakdown,
        },
    }

    # ── Business context enrichment (runs in parallel) ──────────────────────
    bp = business_profile or {}
    page_id = bp.get("facebook_page_id", "")
    competitor_page_ids = [
        p.strip() for p in bp.get("competitor_page_ids", "").split(",") if p.strip()
    ]

    enrichment_tasks = []
    enrichment_labels = []

    if page_id:
        enrichment_tasks.append(fetch_page_stats(page_id, token))
        enrichment_labels.append("page_stats")
        enrichment_tasks.append(fetch_ad_library(page_id, token))
        enrichment_labels.append("own_ad_library")

    if competitor_page_ids:
        enrichment_tasks.append(fetch_competitor_ads(competitor_page_ids, token))
        enrichment_labels.append("competitor_ads")

    if website_url:
        from services.web_scraper import scrape_website
        enrichment_tasks.append(scrape_website(website_url))
        enrichment_labels.append("website")

    # GHL enrichment — always run when GHL is configured
    if settings.GHL_API_KEY and settings.GHL_LOCATION_ID:
        enrichment_tasks.append(fetch_conversation_insights(days=60))
        enrichment_labels.append("conversation_insights")
        enrichment_tasks.append(fetch_ltv_insights(days_back=180))
        enrichment_labels.append("ltv_insights")

    if enrichment_tasks:
        enrichment_results = await asyncio.gather(*enrichment_tasks, return_exceptions=True)
        business_context: dict = {}
        if bp:
            business_context["profile"] = {
                "industry": bp.get("industry", ""),
                "description": bp.get("description", ""),
                "target_customer": bp.get("target_customer", ""),
                "avg_order_value": bp.get("avg_order_value"),
                "primary_goal": bp.get("primary_goal", ""),
            }
        for label, result in zip(enrichment_labels, enrichment_results):
            if isinstance(result, Exception):
                logger.warning(f"Enrichment task '{label}' failed: {result}")
                business_context[label] = {"error": str(result)[:200]}
            else:
                business_context[label] = result
        payload["business_context"] = business_context
    elif bp:
        # No async enrichment but profile data still present
        payload["business_context"] = {
            "profile": {
                "industry": bp.get("industry", ""),
                "description": bp.get("description", ""),
                "target_customer": bp.get("target_customer", ""),
                "avg_order_value": bp.get("avg_order_value"),
                "primary_goal": bp.get("primary_goal", ""),
            }
        }

    # Inject business_notes and report_context into business_context
    if business_notes or report_notes:
        if "business_context" not in payload:
            payload["business_context"] = {}
        if business_notes:
            payload["business_context"]["business_notes"] = business_notes
        if report_notes:
            payload["business_context"]["report_context"] = report_notes

    payload = _truncate_payload(payload)
    return payload


async def run_audit(
    report_id: int,
    account_id: str,
    token: str,
    db: Any,
    models_to_run: list[str],
    business_profile: dict | None = None,
    website_url: str | None = None,
    business_notes: str | None = None,
    report_notes: str | None = None,
) -> None:
    """Full audit workflow. Updates AuditReport row when done."""
    try:
        # 1. Fetch all Meta data + enrichment
        logger.info(f"Audit {report_id}: building payload for account {account_id}")
        payload = await build_audit_payload(
            account_id, token,
            business_profile=business_profile,
            website_url=website_url,
            business_notes=business_notes,
            report_notes=report_notes,
        )

        # 2. Serialize payload
        payload_str = json.dumps(payload)
        logger.info(
            f"Audit {report_id}: payload built ({len(payload_str):,} chars), "
            f"running AI analysis with models: {models_to_run}"
        )

        # 3. Run AI analyses concurrently
        analysis_tasks: list[Any] = []
        analysis_labels: list[str] = []

        if "claude" in models_to_run:
            analysis_tasks.append(analyze_with_claude(payload_str, settings.CLAUDE_API_KEY))
            analysis_labels.append("claude")

        if "openai" in models_to_run:
            openai_key = getattr(settings, "OPENAI_API_KEY", "")
            analysis_tasks.append(analyze_with_openai(payload_str, openai_key))
            analysis_labels.append("openai")

        analysis_results_raw = await asyncio.gather(*analysis_tasks, return_exceptions=True)

        analyses: dict[str, Any] = {}
        for label, result in zip(analysis_labels, analysis_results_raw):
            if isinstance(result, Exception):
                logger.error(f"Audit {report_id}: {label} analysis raised exception: {result}")
                analyses[label] = {"error": str(result)}
            else:
                analyses[label] = result

        # 4. Query previous completed report for the same account (for PDF comparison)
        prev_report = (
            db.query(AuditReport)
            .filter(
                AuditReport.account_id == account_id,
                AuditReport.status == "completed",
                AuditReport.id != report_id,
            )
            .order_by(AuditReport.id.desc())
            .first()
        )

        # 5. Compute summary stats
        campaigns_7d = payload["windows"]["7d"]["campaigns"]
        campaigns_30d = payload["windows"]["30d"]["campaigns"]

        total_spend_7d = sum(c["spend"] for c in campaigns_7d)
        total_spend_30d_stat = sum(c["spend"] for c in campaigns_30d)
        total_conversions_7d = sum(c["primary_action_count"] for c in campaigns_7d)
        total_conversions_30d = sum(c["primary_action_count"] for c in campaigns_30d)
        total_impressions_7d = sum(c["impressions"] for c in campaigns_7d)
        total_impressions_30d = sum(c["impressions"] for c in campaigns_30d)
        total_clicks_7d = sum(c["clicks"] for c in campaigns_7d)
        total_clicks_30d = sum(c["clicks"] for c in campaigns_30d)

        avg_cpa_30d = (
            round(total_spend_30d_stat / total_conversions_30d, 4)
            if total_conversions_30d > 0
            else None
        )
        avg_ctr_30d = (
            round(total_clicks_30d / total_impressions_30d * 100, 4)
            if total_impressions_30d > 0
            else None
        )
        total_roas_value_30d = sum(c["primary_action_value"] for c in campaigns_30d)
        avg_roas_30d = (
            round(total_roas_value_30d / total_spend_30d_stat, 4)
            if total_spend_30d_stat > 0
            else None
        )
        campaign_count = len(campaigns_30d)
        audience_count = len(payload.get("audiences", []))

        summary_stats = {
            "total_spend_7d": round(total_spend_7d, 2),
            "total_spend_30d": round(total_spend_30d_stat, 2),
            "total_conversions_7d": total_conversions_7d,
            "total_conversions_30d": total_conversions_30d,
            "total_impressions_7d": total_impressions_7d,
            "total_impressions_30d": total_impressions_30d,
            "total_clicks_7d": total_clicks_7d,
            "total_clicks_30d": total_clicks_30d,
            "avg_cpa_30d": avg_cpa_30d,
            "avg_ctr_30d": avg_ctr_30d,
            "avg_roas_30d": avg_roas_30d,
            "campaign_count": campaign_count,
            "audience_count": audience_count,
        }

        account_info = payload.get("account", {})

        # 6. Generate PDF
        pdf_bytes = None
        pdf_filename = None
        try:
            from services.audit_pdf import generate_pdf

            pdf_bytes = generate_pdf(
                account_name=account_info.get("name", account_id),
                metrics=summary_stats,
                raw_metrics=payload,
                analyses=analyses,
                prev_report=prev_report,
            )
            pdf_filename = (
                f"audit_{account_id}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf"
            )
        except Exception as e:
            logger.error(f"PDF generation failed: {e}")
            pdf_bytes = None
            pdf_filename = None

        # 7. Update AuditReport row
        report = db.query(AuditReport).filter(AuditReport.id == report_id).first()
        if report:
            report.status = "completed"
            report.raw_metrics = payload
            report.analyses = analyses
            report.pdf_report = pdf_bytes
            report.pdf_filename = pdf_filename
            report.total_spend_7d = summary_stats["total_spend_7d"]
            report.total_spend_30d = summary_stats["total_spend_30d"]
            report.total_conversions_7d = summary_stats["total_conversions_7d"]
            report.total_conversions_30d = summary_stats["total_conversions_30d"]
            report.total_impressions_7d = summary_stats["total_impressions_7d"]
            report.total_impressions_30d = summary_stats["total_impressions_30d"]
            report.total_clicks_7d = summary_stats["total_clicks_7d"]
            report.total_clicks_30d = summary_stats["total_clicks_30d"]
            report.avg_cpa_30d = summary_stats["avg_cpa_30d"]
            report.avg_ctr_30d = summary_stats["avg_ctr_30d"]
            report.avg_roas_30d = summary_stats["avg_roas_30d"]
            report.campaign_count = summary_stats["campaign_count"]
            report.audience_count = summary_stats["audience_count"]
            report.models_used = ",".join(models_to_run)
            db.commit()
            logger.info(f"Audit {report_id}: completed and saved to database")

        # 8. Send email notification
        try:
            from services.email_service import send_audit_email

            send_audit_email(
                account_id=account_id,
                account_name=account_info.get("name", account_id),
                report_id=report_id,
                metrics=summary_stats,
                pdf_bytes=pdf_bytes,
                pdf_filename=pdf_filename,
            )
        except Exception as e:
            logger.error(f"Failed to send audit email: {e}")

    except Exception as e:
        logger.error(f"Audit {report_id}: failed with error: {e}", exc_info=True)
        report = db.query(AuditReport).filter(AuditReport.id == report_id).first()
        if report:
            report.status = "failed"
            report.error_message = str(e)
            db.commit()
        raise


async def reanalyze_audit(
    report_id: int,
    db: Any,
    models_to_run: list[str],
    business_profile: dict | None = None,
    business_notes: str | None = None,
) -> None:
    """Re-run AI analysis on existing stored raw_metrics. No Meta API calls."""
    report = db.query(AuditReport).filter(AuditReport.id == report_id).first()
    old_analyses = dict(report.analyses or {}) if report else {}

    try:
        if not report or not report.raw_metrics:
            raise ValueError(f"Report {report_id} has no stored metrics to re-analyze")

        # 1. Start from stored payload, rebuild business_context from current account data
        payload = dict(report.raw_metrics)
        existing_bc = payload.get("business_context", {})

        business_context: dict = {}

        bp = business_profile or {}
        if bp:
            business_context["profile"] = {
                "industry": bp.get("industry", ""),
                "description": bp.get("description", ""),
                "target_customer": bp.get("target_customer", ""),
                "avg_order_value": bp.get("avg_order_value"),
                "primary_goal": bp.get("primary_goal", ""),
            }

        # Preserve enrichment that doesn't need re-fetching
        for key in ("page_stats", "own_ad_library", "competitor_ads", "website",
                    "conversation_insights", "ltv_insights"):
            if key in existing_bc:
                business_context[key] = existing_bc[key]

        if business_notes:
            business_context["business_notes"] = business_notes
        context_str = _format_contexts(list(report.audit_contexts or [])) or report.report_notes
        if context_str:
            business_context["report_context"] = context_str

        if business_context:
            payload["business_context"] = business_context

        # 2. Run AI analyses
        payload_str = json.dumps(payload)
        logger.info(
            f"Reanalysis {report_id}: payload={len(payload_str):,} chars, models={models_to_run}"
        )

        analysis_tasks: list[Any] = []
        analysis_labels: list[str] = []

        if "claude" in models_to_run:
            analysis_tasks.append(analyze_with_claude(payload_str, settings.CLAUDE_API_KEY))
            analysis_labels.append("claude")

        if "openai" in models_to_run:
            openai_key = getattr(settings, "OPENAI_API_KEY", "")
            analysis_tasks.append(analyze_with_openai(payload_str, openai_key))
            analysis_labels.append("openai")

        analysis_results_raw = await asyncio.gather(*analysis_tasks, return_exceptions=True)

        analyses: dict[str, Any] = {}
        for label, result in zip(analysis_labels, analysis_results_raw):
            if isinstance(result, Exception):
                logger.error(f"Reanalysis {report_id}: {label} raised exception: {result}")
                analyses[label] = {"error": str(result)}
            else:
                analyses[label] = result

        # 3. Previous report for PDF comparison
        prev_report = (
            db.query(AuditReport)
            .filter(
                AuditReport.account_id == report.account_id,
                AuditReport.status == "completed",
                AuditReport.id != report_id,
            )
            .order_by(AuditReport.id.desc())
            .first()
        )

        # 4. Reuse stored summary stats (Meta data unchanged)
        metrics = {
            "total_spend_7d":       float(report.total_spend_7d)       if report.total_spend_7d       else 0,
            "total_spend_30d":      float(report.total_spend_30d)      if report.total_spend_30d      else 0,
            "total_conversions_7d": report.total_conversions_7d  or 0,
            "total_conversions_30d":report.total_conversions_30d or 0,
            "total_impressions_7d": report.total_impressions_7d  or 0,
            "total_impressions_30d":report.total_impressions_30d or 0,
            "total_clicks_7d":      report.total_clicks_7d       or 0,
            "total_clicks_30d":     report.total_clicks_30d      or 0,
            "avg_cpa_30d":          float(report.avg_cpa_30d)          if report.avg_cpa_30d          else None,
            "avg_ctr_30d":          float(report.avg_ctr_30d)          if report.avg_ctr_30d          else None,
            "avg_roas_30d":         float(report.avg_roas_30d)         if report.avg_roas_30d         else None,
            "campaign_count":       report.campaign_count  or 0,
            "audience_count":       report.audience_count  or 0,
        }

        # 5. Regenerate PDF
        pdf_bytes = None
        pdf_filename = None
        try:
            from services.audit_pdf import generate_pdf
            account_name = payload.get("account", {}).get("name", report.account_id)
            pdf_bytes = generate_pdf(
                account_name=account_name,
                metrics=metrics,
                raw_metrics=payload,
                analyses=analyses,
                prev_report=prev_report,
            )
            pdf_filename = (
                f"audit_{report.account_id}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf"
            )
        except Exception as e:
            logger.error(f"Reanalysis {report_id}: PDF generation failed: {e}")

        # 6. Update report — preserve original generated_at timestamp
        report.analyses    = analyses
        report.raw_metrics = payload
        report.models_used = ",".join(models_to_run)
        report.status      = "completed"
        report.error_message = None
        if pdf_bytes:
            report.pdf_report  = pdf_bytes
            report.pdf_filename = pdf_filename
        db.commit()
        logger.info(f"Reanalysis {report_id}: completed")

    except Exception as e:
        logger.error(f"Reanalysis {report_id}: failed: {e}", exc_info=True)
        # Restore old analyses so the report stays usable
        report = db.query(AuditReport).filter(AuditReport.id == report_id).first()
        if report:
            report.analyses = old_analyses
            report.status = "completed"
            report.error_message = f"Re-analysis failed: {str(e)}"
            db.commit()
        raise
