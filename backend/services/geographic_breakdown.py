"""
Geographic breakdown enrichment for the audit.

Combines Meta's `region` breakdown (state-level ad spend/impressions/clicks)
with GHL contact distribution and matched conversion revenue to identify:
  - States where you're spending heavily but converting poorly (wasted)
  - States with high conversion rate but low spend (opportunity)
  - States where spend and conversions align (working)
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, TYPE_CHECKING

import httpx
from sqlalchemy.orm import Session

from services.geo_helpers import normalize_state, state_display_name
from services.area_code_state import state_from_phone

if TYPE_CHECKING:
    from services.credential_resolver import AccountCredentials

logger = logging.getLogger(__name__)

BASE_URL = "https://graph.facebook.com/v21.0"


async def _fetch_meta_region_breakdown(
    account_id: str,
    token: str,
    since: str,
    until: str,
) -> list[dict]:
    """Fetch Meta insights broken down by region (US states + others)."""
    rows: list[dict] = []
    url = f"{BASE_URL}/{account_id}/insights"
    params = {
        "access_token": token,
        "level": "account",
        "fields": "spend,impressions,clicks,actions,action_values",
        "time_range": json.dumps({"since": since, "until": until}),
        "breakdowns": "region",
        "filtering": json.dumps([{"field": "country", "operator": "IN", "value": ["US"]}]),
        "limit": 500,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        while url:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning(f"Meta region breakdown failed: {resp.status_code} {resp.text[:200]}")
                break
            data = resp.json()
            rows.extend(data.get("data", []))
            paging = data.get("paging", {}).get("next")
            if paging:
                url = paging
                params = {}  # next URL has params encoded
            else:
                break

    return rows


def _resolve_contact_state(contact: dict) -> str | None:
    """
    Best-effort state resolution for a GHL contact.

    Priority:
    1. contact.state (only populated when detail endpoint is used — rare in list)
    2. Phone area code → state (NANPA lookup) — works for ~95% of US contacts
    """
    code = normalize_state(contact.get("state"))
    if code:
        return code
    return state_from_phone(contact.get("phone"))


def _aggregate_contacts_by_state(contacts: list[dict]) -> dict[str, int]:
    """Count GHL contacts per state code."""
    counts: dict[str, int] = {}
    for c in contacts or []:
        code = _resolve_contact_state(c)
        if not code:
            continue
        counts[code] = counts.get(code, 0) + 1
    return counts


def _extract_contact_ltv(contact: dict, ltv_field_uuid: str | None) -> float:
    """Pull the LTV value from a GHL contact's customFields list."""
    if not ltv_field_uuid:
        return 0.0
    for cf in contact.get("customFields") or []:
        if cf.get("id") == ltv_field_uuid:
            try:
                val = str(cf.get("value") or "0").replace(",", "").replace("$", "")
                v = float(val)
                return max(v, 0.0)
            except (ValueError, TypeError):
                return 0.0
    return 0.0


def _aggregate_ltv_by_state(
    contacts: list[dict],
    ltv_field_uuid: str | None,
) -> dict[str, dict[str, float | int]]:
    """
    For each state: sum LTV across contacts, count contacts with non-zero LTV.
    Returns {state: {total_ltv, paying_contacts}}.
    """
    out: dict[str, dict[str, float | int]] = {}
    if not ltv_field_uuid:
        return out
    for c in contacts or []:
        code = _resolve_contact_state(c)
        if not code:
            continue
        ltv = _extract_contact_ltv(c, ltv_field_uuid)
        entry = out.setdefault(code, {"total_ltv": 0.0, "paying_contacts": 0})
        if ltv > 0:
            entry["total_ltv"] = float(entry["total_ltv"]) + ltv
            entry["paying_contacts"] = int(entry["paying_contacts"]) + 1
    return out


def _aggregate_conversions_by_state(
    db: Session,
    contacts_by_id: dict[str, dict],
    days_back: int = 30,
) -> dict[str, dict[str, Any]]:
    """
    For each state, count MatchedConversions and sum their revenue.
    We join via ghl_contact_id → contact.state.
    """
    from models import MatchedConversion

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    rows = (
        db.query(MatchedConversion)
        .filter(MatchedConversion.stripe_created_at >= cutoff)
        .all()
    )

    by_state: dict[str, dict[str, Any]] = {}
    unmatched_count = 0

    for conv in rows:
        contact = contacts_by_id.get(conv.ghl_contact_id) if conv.ghl_contact_id else None
        if not contact:
            unmatched_count += 1
            continue
        code = _resolve_contact_state(contact)
        if not code:
            continue
        entry = by_state.setdefault(code, {"conversions": 0, "revenue_cents": 0})
        entry["conversions"] += 1
        entry["revenue_cents"] += conv.amount_cents or 0

    if unmatched_count:
        logger.info(f"{unmatched_count} conversions could not be tied to a GHL state (no contact match)")

    return by_state


def _parse_meta_region_row(row: dict) -> tuple[str | None, dict]:
    """
    Convert a single Meta breakdown row to (state_code, metrics).
    Meta returns "region" as the state name in English (e.g. "California").
    """
    state_code = normalize_state(row.get("region"))
    if not state_code:
        return None, {}

    spend = float(row.get("spend", 0) or 0)
    impressions = int(row.get("impressions", 0) or 0)
    clicks = int(row.get("clicks", 0) or 0)

    # Count Meta-reported conversions (any action type)
    actions = row.get("actions", []) or []
    meta_conversions = 0
    for a in actions:
        t = a.get("action_type", "")
        if t in {"purchase", "lead", "complete_registration", "offsite_conversion.fb_pixel_purchase"}:
            meta_conversions += int(float(a.get("value", 0) or 0))

    return state_code, {
        "spend": round(spend, 2),
        "impressions": impressions,
        "clicks": clicks,
        "meta_conversions": meta_conversions,
    }


def _effective_roas(row: dict) -> float:
    """
    Use the strongest revenue signal available: max(window stripe revenue, total LTV).
    Lifetime value is the more truthful indicator for paid acquisition since
    customers don't always pay in the same window they were acquired in.
    """
    spend = row["spend"]
    if spend <= 0:
        return 0.0
    revenue = max(row.get("revenue_30d", 0) or 0, row.get("total_ltv", 0) or 0)
    return revenue / spend


def _classify_state(row: dict, account_avg_cpa: float | None) -> str | None:
    """
    Tag each state by ROAS tier:

    high_roas:    ROAS >= 2.0 on >= $50 spend   (scale these up — clear winners)
    medium_roas:  1.0 <= ROAS < 2.0             (profitable, hold steady)
    low_roas:     0 < ROAS < 1.0                (losing money on every $)
    no_roas:      spend > 0 AND zero paying customers AND zero revenue/LTV
                  (silent drain — cut these immediately)
    untapped:     total_ltv >= $200 on < $50 spend  (customers exist, no ads running there)
    """
    spend = row["spend"] or 0
    total_ltv = row.get("total_ltv", 0) or 0
    paying = row.get("paying_contacts", 0) or 0
    conversions = row.get("conversions", 0) or 0
    revenue_30d = row.get("revenue_30d", 0) or 0
    effective_paying = max(paying, conversions)
    has_revenue_signal = total_ltv > 0 or revenue_30d > 0

    # Zero return on any spend — silent or loud drain
    if spend > 0 and effective_paying == 0 and not has_revenue_signal:
        return "no_roas"

    # Untapped — historical customer base, no current ads running
    if spend < 50 and total_ltv >= 200:
        return "untapped"

    # Sub-$50 spend without strong LTV — too little signal
    if spend < 50:
        return None

    roas = _effective_roas(row)

    if roas >= 2.0:
        return "high_roas"
    if roas >= 1.0:
        return "medium_roas"
    if roas > 0:
        return "low_roas"

    return None


async def build_geographic_breakdown(
    account_id: str,
    token: str,
    since: str,
    until: str,
    contacts: list[dict],
    db: Session,
    creds: "AccountCredentials | None" = None,
    ltv_field_uuid: str | None = None,
) -> dict:
    """
    Build a state-level geographic breakdown combining Meta spend data
    with GHL contact + conversion data.

    Returns:
    {
        "states": [
            {
                "state": "CA", "state_name": "California",
                "spend": 4210.00, "impressions": 312000, "clicks": 5200,
                "contacts": 142, "conversions": 18, "revenue": 3240.00,
                "cpa": 234.0, "conversion_rate_pct": 12.7,
                "classification": "wasted" | "opportunity" | "working" | None
            }, ...
        ],
        "summary": {
            "total_states_with_spend": int,
            "total_states_with_contacts": int,
            "wasted": [top 5 wasted states with rationale],
            "opportunity": [top 5 opportunity states with rationale],
            "working": [top 5 working states],
        }
    }
    """
    # Fetch Meta region data (in parallel-ready form)
    meta_rows = await _fetch_meta_region_breakdown(account_id, token, since, until)

    # Auto-discover LTV field if not provided
    if ltv_field_uuid is None:
        try:
            from api.ghl_client import get_custom_fields
            fields = await get_custom_fields(creds=creds)
            ltv_field = next((f for f in fields if f.get("fieldKey") == "contact.ltv"), None)
            ltv_field_uuid = ltv_field.get("id") if ltv_field else None
        except Exception as e:
            logger.warning(f"Could not resolve LTV custom field: {e}")
            ltv_field_uuid = None

    # ── Enrich paying contacts with precise billing address ──────────────────
    # Cascade for state assignment: Stripe billing > GHL detail address > phone area code.
    # Only enrich contacts with non-zero LTV — keeps the run fast (~5-15s for typical
    # paying cohort), since they're the ones whose state placement actually matters
    # for revenue/ROAS analysis.
    paying = [c for c in contacts if _extract_contact_ltv(c, ltv_field_uuid) > 0]
    if paying:
        logger.info(f"Enriching {len(paying)} paying contacts with billing address data")

        # 1) Stripe billing — uses existing fuzzy match (stripe_transactions.ghl_contact_id)
        try:
            from services.stripe_address_resolver import resolve_addresses_for_contacts
            contact_ids = [c.get("id") for c in paying if c.get("id")]
            stripe_addr = await resolve_addresses_for_contacts(contact_ids, db, creds=creds)
            stripe_hits = 0
            for c in paying:
                cid = c.get("id")
                if cid in stripe_addr:
                    addr = stripe_addr[cid]
                    # Stripe billing address takes precedence — overwrite even if contact had data
                    c["state"] = addr.get("state") or c.get("state")
                    c["postalCode"] = addr.get("postal_code") or c.get("postalCode")
                    c["city"] = addr.get("city") or c.get("city")
                    stripe_hits += 1
            logger.info(f"Stripe billing resolved state for {stripe_hits}/{len(paying)} paying contacts")
        except Exception as e:
            logger.warning(f"Stripe billing enrichment failed: {e}", exc_info=True)

        # 2) GHL contact-detail fetch — for paying contacts still missing state
        still_missing = [c for c in paying if not _resolve_contact_state(c)]
        if still_missing:
            try:
                from api.ghl_client import enrich_contacts_with_address
                await enrich_contacts_with_address(still_missing, creds=creds)
                logger.info(f"GHL detail enriched {len(still_missing)} contacts as Stripe fallback")
            except Exception as e:
                logger.warning(f"GHL detail enrichment failed: {e}")

    # Aggregate GHL contacts by state (uses enriched fields where available)
    contacts_by_state = _aggregate_contacts_by_state(contacts)

    # Aggregate LTV per state from GHL contacts
    ltv_by_state = _aggregate_ltv_by_state(contacts, ltv_field_uuid)

    # Build lookup: ghl_contact_id → contact dict (for conversion join)
    contacts_by_id = {c["id"]: c for c in contacts if c.get("id")}

    # Aggregate conversions by state
    days_back = (datetime.fromisoformat(until) - datetime.fromisoformat(since)).days + 1
    conversions_by_state = _aggregate_conversions_by_state(db, contacts_by_id, days_back=days_back)

    # Merge — index everything by state code
    state_metrics: dict[str, dict[str, Any]] = {}
    for row in meta_rows:
        code, m = _parse_meta_region_row(row)
        if not code:
            continue
        existing = state_metrics.setdefault(code, {
            "spend": 0.0, "impressions": 0, "clicks": 0, "meta_conversions": 0,
        })
        existing["spend"] += m["spend"]
        existing["impressions"] += m["impressions"]
        existing["clicks"] += m["clicks"]
        existing["meta_conversions"] += m["meta_conversions"]

    # Union of all states we have any data for
    all_states = set(state_metrics) | set(contacts_by_state) | set(conversions_by_state) | set(ltv_by_state)

    rows: list[dict] = []
    total_spend = 0.0
    total_conversions = 0
    total_ltv = 0.0

    for code in all_states:
        meta = state_metrics.get(code, {"spend": 0.0, "impressions": 0, "clicks": 0, "meta_conversions": 0})
        contact_count = contacts_by_state.get(code, 0)
        conv_data = conversions_by_state.get(code, {"conversions": 0, "revenue_cents": 0})
        ltv_data = ltv_by_state.get(code, {"total_ltv": 0.0, "paying_contacts": 0})

        spend = round(meta["spend"], 2)
        impressions = meta["impressions"]
        clicks = meta["clicks"]
        conversions_30d = conv_data["conversions"]
        revenue_30d = round(conv_data["revenue_cents"] / 100, 2)
        total_state_ltv = round(float(ltv_data["total_ltv"]), 2)
        paying_contacts = int(ltv_data["paying_contacts"])

        # Use Stripe conversions if present, else GHL paying contacts, else Meta-reported
        effective_conversions = (
            conversions_30d if conversions_30d > 0
            else paying_contacts if paying_contacts > 0
            else meta["meta_conversions"]
        )

        # Combined revenue signal: prefer 30d Stripe revenue if available, else total LTV
        # We expose both fields so the UI can toggle.
        revenue_display = revenue_30d if revenue_30d > 0 else total_state_ltv

        cpa = round(spend / effective_conversions, 2) if effective_conversions > 0 and spend > 0 else None
        conversion_rate_pct = (
            round(effective_conversions / contact_count * 100, 1)
            if contact_count > 0 else 0.0
        )
        roas = round(revenue_display / spend, 2) if spend > 0 and revenue_display > 0 else None
        avg_ltv = round(total_state_ltv / paying_contacts, 2) if paying_contacts > 0 else None

        rows.append({
            "state": code,
            "state_name": state_display_name(code),
            "spend": spend,
            "impressions": impressions,
            "clicks": clicks,
            "contacts": contact_count,
            "paying_contacts": paying_contacts,
            "conversions": conversions_30d,
            "meta_reported_conversions": meta["meta_conversions"],
            "revenue_30d": revenue_30d,
            "total_ltv": total_state_ltv,
            "avg_ltv": avg_ltv,
            "revenue": revenue_display,
            "cpa": cpa,
            "roas": roas,
            "conversion_rate_pct": conversion_rate_pct,
        })

        total_spend += spend
        total_conversions += effective_conversions
        total_ltv += total_state_ltv

    # Sort by spend desc
    rows.sort(key=lambda r: r["spend"], reverse=True)

    # Compute account-wide average CPA for classification
    account_avg_cpa = (total_spend / total_conversions) if total_conversions > 0 and total_spend > 0 else None

    # Classify each state
    for row in rows:
        row["classification"] = _classify_state(row, account_avg_cpa)

    # Build summary buckets
    wasted = sorted(
        [r for r in rows if r["classification"] == "wasted"],
        key=lambda r: r["spend"], reverse=True,
    )[:5]
    opportunity = sorted(
        [r for r in rows if r["classification"] == "opportunity"],
        key=lambda r: r["conversion_rate_pct"], reverse=True,
    )[:5]
    working = sorted(
        [r for r in rows if r["classification"] == "working"],
        key=lambda r: r["spend"], reverse=True,
    )[:5]

    # Account-wide ROAS — prefer Stripe revenue if any, else fall back to total LTV (lifetime)
    total_revenue_30d = sum(r["revenue_30d"] for r in rows)
    account_roas = round(total_revenue_30d / total_spend, 2) if total_spend > 0 and total_revenue_30d > 0 else None
    ltv_roas = round(total_ltv / total_spend, 2) if total_spend > 0 and total_ltv > 0 else None

    # Monthly run-rate — answers "what is this account actually spending per month?"
    months_in_window = max(days_back / 30.0, 1.0 / 30.0)
    avg_monthly_spend = round(total_spend / months_in_window, 2) if total_spend > 0 else 0.0

    narrative = _build_narrative(
        rows,
        total_spend=total_spend,
        total_conversions=total_conversions,
        total_ltv=total_ltv,
        total_revenue_30d=total_revenue_30d,
        account_avg_cpa=account_avg_cpa,
        account_roas=account_roas,
        ltv_roas=ltv_roas,
        window_days=days_back,
        avg_monthly_spend=avg_monthly_spend,
    )

    return {
        "states": rows,
        "narrative": narrative,
        "window_days": days_back,
        "since": since,
        "until": until,
        "summary": {
            "window_days": days_back,
            "total_spend": round(total_spend, 2),
            "avg_monthly_spend": avg_monthly_spend,
            "total_revenue_in_window": round(total_revenue_30d, 2),
            "total_conversions": total_conversions,
            "total_ltv": round(total_ltv, 2),
            "account_roas": account_roas,
            "ltv_roas": ltv_roas,
            "account_avg_cpa": round(account_avg_cpa, 2) if account_avg_cpa else None,
            # Backward-compat aliases (deprecated)
            "total_spend_30d": round(total_spend, 2),
            "total_conversions_30d": total_conversions,
            "states_with_spend": sum(1 for r in rows if r["spend"] > 0),
            "states_with_contacts": sum(1 for r in rows if r["contacts"] > 0),
            "states_with_conversions": sum(1 for r in rows if r["conversions"] > 0),
            "states_with_ltv": sum(1 for r in rows if r["total_ltv"] > 0),
            "wasted": [
                {"state": r["state"], "state_name": r["state_name"], "spend": r["spend"],
                 "conversions": r["conversions"], "cpa": r["cpa"], "total_ltv": r["total_ltv"]}
                for r in wasted
            ],
            "opportunity": [
                {"state": r["state"], "state_name": r["state_name"], "spend": r["spend"],
                 "conversions": r["conversions"], "conversion_rate_pct": r["conversion_rate_pct"],
                 "total_ltv": r["total_ltv"]}
                for r in opportunity
            ],
            "working": [
                {"state": r["state"], "state_name": r["state_name"], "spend": r["spend"],
                 "conversions": r["conversions"], "cpa": r["cpa"], "total_ltv": r["total_ltv"]}
                for r in working
            ],
        },
    }


def _window_phrase(window_days: int) -> str:
    """Human-friendly window label: '30 days' / '90 days' / '6 months'."""
    if window_days <= 0:
        return "the selected window"
    if window_days % 365 == 0 and window_days >= 365:
        years = window_days // 365
        return f"{years} year" + ("s" if years > 1 else "")
    if window_days % 30 == 0 and window_days >= 60:
        months = window_days // 30
        return f"{months} months"
    return f"{window_days} days"


def _state_roas(row: dict) -> float:
    """Effective ROAS using the stronger revenue signal."""
    spend = row.get("spend", 0) or 0
    if spend <= 0:
        return 0.0
    revenue = max(row.get("revenue_30d", 0) or 0, row.get("total_ltv", 0) or 0)
    return revenue / spend


def _build_narrative(
    rows: list[dict],
    total_spend: float,
    total_conversions: int,
    total_ltv: float,
    total_revenue_30d: float,
    account_avg_cpa: float | None,
    account_roas: float | None,
    ltv_roas: float | None,
    window_days: int,
    avg_monthly_spend: float = 0.0,
) -> dict:
    """
    Build plain-English insights:
      - WORKING:    high-ROAS / low-CPA geos to keep funding
      - WASTED:     burned budget, action = exclude or split off
      - OPPORTUNITY: where to point campaign funnels next

    The window label is woven in so users know what time range these insights cover.
    """
    win = _window_phrase(window_days)
    spending_rows = [r for r in rows if r["spend"] > 0]
    ltv_rows = [r for r in rows if r["total_ltv"] > 0]

    if not spending_rows and not ltv_rows:
        return {
            "summary": f"No Meta ad spend or GHL revenue data found for US states in the last {win}.",
        }

    # ── Bucket states by new ROAS tier ────────────────────────────────────────
    high_roas = sorted([r for r in rows if r["classification"] == "high_roas"],
                       key=lambda r: _state_roas(r), reverse=True)
    medium_roas = sorted([r for r in rows if r["classification"] == "medium_roas"],
                         key=lambda r: _state_roas(r), reverse=True)
    low_roas = sorted([r for r in rows if r["classification"] == "low_roas"],
                      key=lambda r: r["spend"], reverse=True)
    no_roas = sorted([r for r in rows if r["classification"] == "no_roas"],
                     key=lambda r: r["spend"], reverse=True)
    untapped = sorted([r for r in rows if r["classification"] == "untapped"],
                      key=lambda r: r["total_ltv"], reverse=True)

    # ── Totals ────────────────────────────────────────────────────────────────
    high_spend = sum(r["spend"] for r in high_roas)
    high_ltv = sum(r["total_ltv"] for r in high_roas)
    no_roas_spend = sum(r["spend"] for r in no_roas)
    low_roas_spend = sum(r["spend"] for r in low_roas)
    recoverable = no_roas_spend + low_roas_spend

    # Average ROAS of high-roas + untapped states — for reallocation projection
    upside_states = [r for r in high_roas if r["spend"] > 0]
    upside_avg_roas = (
        sum(_state_roas(r) for r in upside_states) / len(upside_states)
        if upside_states else 0.0
    )
    potential_revenue = recoverable * upside_avg_roas if upside_avg_roas > 0 else 0

    # ── Inclusion / exclusion lists (Meta-paste-friendly) ────────────────────
    # Include = High ROAS + Untapped (scale these up)
    # Exclude = No ROAS + Low ROAS (cut the bleed and the losses)
    inclusion_rows = list(high_roas) + list(untapped)
    exclusion_rows = list(no_roas) + list(low_roas)

    inclusion_names = [r["state_name"] for r in inclusion_rows if r["state_name"]]
    exclusion_names = [r["state_name"] for r in exclusion_rows if r["state_name"]]
    inclusion_codes = [r["state"] for r in inclusion_rows if r["state"]]
    exclusion_codes = [r["state"] for r in exclusion_rows if r["state"]]
    inclusion_csv = ", ".join(inclusion_names)
    exclusion_csv = ", ".join(exclusion_names)

    # ── Build the formatted summary ──────────────────────────────────────────
    sections: list[str] = []

    # Header line
    if total_spend > 0:
        rate_phrase = f"~${avg_monthly_spend:,.0f}/month" if avg_monthly_spend > 0 else ""
        roas_phrase = ""
        if total_ltv > 0 and ltv_roas:
            roas_phrase = f" · {ltv_roas:.2f}× LTV ROAS"
        elif account_roas:
            roas_phrase = f" · {account_roas:.2f}× ROAS"
        header = f"LAST {win.upper()} · ${total_spend:,.0f} spent"
        if rate_phrase:
            header += f" · {rate_phrase}"
        header += roas_phrase
        sections.append(header)
        if total_ltv > 0:
            sections.append(f"Customers in these geographies represent ${total_ltv:,.0f} in lifetime value.")

    # HIGH ROAS — keep + scale up
    if high_roas:
        lines = ["★ HIGH ROAS — scale these up (consider 15-25% budget increases):"]
        for r in high_roas:
            lines.append(
                f"   • {r['state_name']} — ${r['spend']:,.0f} spent → ${r['total_ltv']:,.0f} LTV "
                f"({_state_roas(r):.1f}× ROAS, {r['paying_contacts']} paying)"
            )
        if high_spend > 0:
            lines.append(f"   → Total: ${high_ltv:,.0f} returned on ${high_spend:,.0f} spent")
        sections.append("\n".join(lines))

    # MEDIUM ROAS — hold steady
    if medium_roas:
        lines = ["~ MEDIUM ROAS — profitable, hold steady (don't change budgets unless data improves):"]
        for r in medium_roas:
            lines.append(
                f"   • {r['state_name']} — ${r['spend']:,.0f} → ${r['total_ltv']:,.0f} LTV ({_state_roas(r):.1f}× ROAS)"
            )
        sections.append("\n".join(lines))

    # LOW ROAS — losing money
    if low_roas:
        lines = [f"▼ LOW ROAS — losing money (${low_roas_spend:,.0f} at <1× return, consider excluding):"]
        for r in low_roas:
            lines.append(
                f"   • {r['state_name']} — ${r['spend']:,.0f} → ${r['total_ltv']:,.0f} LTV ({_state_roas(r):.2f}× ROAS)"
            )
        sections.append("\n".join(lines))

    # NO ROAS — silent drain (this is what the user called "silent bleed" — keep this category)
    if no_roas:
        no_roas_names = ", ".join(r["state_name"] for r in no_roas)
        sections.append(
            f"✗ NO ROAS — zero return on spend ({len(no_roas)} states, ${no_roas_spend:,.0f} total):\n"
            f"   {no_roas_names}\n"
            "   → Cut these. Add to Ad Set → Locations → Exclude."
        )

    # UNTAPPED — historical customers, no current ads
    if untapped:
        lines = ["◇ UNTAPPED — existing customers here, no current ad spend:"]
        for r in untapped[:8]:
            lines.append(
                f"   • {r['state_name']} — ${r['total_ltv']:,.0f} historical LTV, only ${r['spend']:,.0f} spent"
            )
        lines.append("   → Build a state-targeted prospecting campaign + Lookalike-1% from your top-LTV customers here.")
        sections.append("\n".join(lines))

    # REALLOCATION PROJECTION
    if recoverable > 0 and upside_avg_roas > 0:
        sections.append(
            f"⇄ REALLOCATION: cutting the ${recoverable:,.0f} of Low+No ROAS spend and redirecting it to "
            f"High ROAS geos (avg {upside_avg_roas:.1f}× ROAS) projects ~${potential_revenue:,.0f} in additional revenue."
        )

    if not sections:
        sections.append(
            f"Nothing classifies cleanly yet in this {win} window — spend is either too small, too spread out, "
            "or revenue tracking is incomplete. Try widening the time window or running transaction sync."
        )

    return {
        "summary": "\n\n".join(sections),
        "inclusion_states": inclusion_codes,
        "inclusion_state_names": inclusion_names,
        "inclusion_csv": inclusion_csv,
        "exclusion_states": exclusion_codes,
        "exclusion_state_names": exclusion_names,
        "exclusion_csv": exclusion_csv,
        "tier_totals": {
            "high_spend": round(high_spend, 2),
            "high_ltv": round(high_ltv, 2),
            "high_count": len(high_roas),
            "medium_count": len(medium_roas),
            "low_spend": round(low_roas_spend, 2),
            "low_count": len(low_roas),
            "no_roas_spend": round(no_roas_spend, 2),
            "no_roas_count": len(no_roas),
            "untapped_count": len(untapped),
        },
        "reallocation": {
            "recoverable_spend": round(recoverable, 2),
            "upside_avg_roas": round(upside_avg_roas, 2),
            "projected_revenue_gain": round(potential_revenue, 2),
        },
    }
