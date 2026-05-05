"""
Conversion tracking pipeline:
  - GHL attribution extraction (utmFbclid from attributions[])
  - Meta CAPI event construction (zero health context)
  - End-to-end process_conversion orchestration
"""
import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy.orm import Session

from api.ghl_client import get_all_contacts
from config import settings
from models import MatchedConversion
from services.identity_resolver import match_stripe_to_ghl, normalize_phone

logger = logging.getLogger(__name__)


# ── GHL attribution extraction ───────────────────────────────────────────────

def extract_ghl_attribution(contact: dict) -> dict:
    """
    Pull Meta attribution fields from a GHL contact.
    fbclid lives in contact.attributions[].utmFbclid (not attributionSource).
    """
    # fbclid from attributions array
    fbclid = None
    for attr in contact.get("attributions") or []:
        val = attr.get("utmFbclid")
        if val:
            fbclid = val
            break

    # fbp from attributions or custom fields
    fbp = None
    for attr in contact.get("attributions") or []:
        val = attr.get("fbp") or attr.get("_fbp")
        if val:
            fbp = val
            break

    # UTM data from first attribution entry
    first_attr = (contact.get("attributions") or [{}])[0]
    source_url = first_attr.get("url") or first_attr.get("pageUrl") or ""

    utm_source = first_attr.get("utmSource") or _extract_param(source_url, "utm_source")
    utm_medium = first_attr.get("utmMedium") or _extract_param(source_url, "utm_medium")
    utm_campaign = first_attr.get("utmCampaign") or _extract_param(source_url, "utm_campaign")

    return {
        "fbclid": fbclid,
        "fbp": fbp,
        "utm_source": utm_source,
        "utm_medium": utm_medium,
        "utm_campaign": utm_campaign,
        "email": (contact.get("email") or "").lower().strip(),
        "phone": normalize_phone(contact.get("phone") or ""),
        "first_name": (contact.get("firstName") or "").strip(),
        "last_name": (contact.get("lastName") or "").strip(),
        "city": (contact.get("city") or "").strip(),
        "state": (contact.get("state") or "").strip(),
        "zip": (contact.get("postalCode") or "").strip(),
        "country": (contact.get("country") or "US").strip(),
    }


def _extract_param(url: str, param: str) -> str | None:
    try:
        values = parse_qs(urlparse(url).query).get(param, [])
        return values[0] if values else None
    except Exception:
        return None


# ── CAPI event construction ──────────────────────────────────────────────────

def _sha256(value: str) -> str:
    return hashlib.sha256(value.lower().strip().encode()).hexdigest()


def build_capi_event(
    stripe_data: dict,
    ghl_attribution: dict,
    action_source: str = "website",
) -> tuple[dict, str]:
    """
    Build a Meta CAPI event with zero health context.
    No product names, no URLs, no class types — only hashed PII + value.
    action_source: "website" (≤7 days) or "physical_store" (8–90 days).
    """
    event_name = settings.CAPI_EVENT_NAME
    event_id = f"evt_{stripe_data['session_id']}_{uuid.uuid4().hex[:8]}"

    user_data: dict = {}

    email = ghl_attribution.get("email") or stripe_data.get("email", "")
    if email:
        user_data["em"] = [_sha256(email)]

    phone = ghl_attribution.get("phone") or stripe_data.get("phone", "")
    if phone and len(phone) >= 10:
        user_data["ph"] = [_sha256(f"1{phone}")]

    fn = ghl_attribution.get("first_name") or ""
    if not fn and stripe_data.get("name"):
        parts = stripe_data["name"].split()
        fn = parts[0] if parts else ""
    if fn:
        user_data["fn"] = [_sha256(fn)]

    ln = ghl_attribution.get("last_name") or ""
    if not ln and stripe_data.get("name"):
        parts = stripe_data["name"].split()
        ln = parts[-1] if len(parts) > 1 else ""
    if ln:
        user_data["ln"] = [_sha256(ln)]

    if ghl_attribution.get("city"):
        user_data["ct"] = [_sha256(ghl_attribution["city"])]
    if ghl_attribution.get("state"):
        user_data["st"] = [_sha256(ghl_attribution["state"])]
    if ghl_attribution.get("zip"):
        user_data["zp"] = [_sha256(ghl_attribution["zip"])]
    if ghl_attribution.get("country"):
        user_data["country"] = [_sha256(ghl_attribution["country"])]

    # Attribution identifiers — NOT hashed, Meta requires raw values
    if ghl_attribution.get("fbclid"):
        user_data["fbc"] = f"fb.1.{int(time.time())}.{ghl_attribution['fbclid']}"
    if ghl_attribution.get("fbp"):
        user_data["fbp"] = ghl_attribution["fbp"]

    event = {
        "event_name": event_name,
        "event_time": int(stripe_data["created_at"].timestamp()),
        "event_id": event_id,
        "action_source": action_source,
        "user_data": user_data,
        "custom_data": {
            "value": stripe_data["amount_cents"] / 100,
            "currency": stripe_data["currency"].upper(),
            # Deliberately no content_name / content_category / content_type
        },
        # Deliberately no event_source_url — no domain association
    }

    return event, event_id


async def send_to_meta_capi(event: dict, dataset_id: str, access_token: str) -> dict:
    url = f"https://graph.facebook.com/v21.0/{dataset_id}/events"
    payload = {"data": [event], "access_token": access_token}
    if settings.CAPI_TEST_EVENT_CODE:
        payload["test_event_code"] = settings.CAPI_TEST_EVENT_CODE

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        result = resp.json()
        if resp.status_code != 200:
            raise Exception(f"CAPI error {resp.status_code}: {result}")
        return result


# ── Full pipeline ────────────────────────────────────────────────────────────

async def process_conversion(
    stripe_session: dict,
    db: Session,
    source: str = "webhook",
) -> dict:
    """
    Full pipeline: extract → match GHL → build CAPI event → send → store.
    Safe to call without Stripe/CAPI credentials — stores record regardless.
    """
    session_id = stripe_session.get("id") or stripe_session.get("session_id", "")

    # Deduplication
    existing = db.query(MatchedConversion).filter_by(stripe_session_id=session_id).first()
    if existing:
        logger.info(f"Duplicate Stripe session {session_id}, skipping")
        return {"status": "duplicate", "id": existing.id}

    # Extract Stripe fields
    stripe_data = _extract_stripe_data(stripe_session)

    # Fetch GHL contacts and run match cascade
    contacts = await get_all_contacts()
    match_result = await match_stripe_to_ghl(stripe_data, contacts, db)
    ghl_contact = match_result["ghl_contact"]

    ghl_attribution: dict = {}
    if ghl_contact:
        ghl_attribution = extract_ghl_attribution(ghl_contact)

    # Build CAPI event
    event, event_id = build_capi_event(stripe_data, ghl_attribution)

    # Persist record before sending (ensures we track even if CAPI fails)
    record = MatchedConversion(
        stripe_session_id=stripe_data["session_id"],
        stripe_customer_id=stripe_data.get("customer_id"),
        stripe_email=stripe_data.get("email"),
        stripe_phone=stripe_data.get("phone"),
        stripe_name=stripe_data.get("name"),
        amount_cents=stripe_data["amount_cents"],
        currency=stripe_data["currency"],
        stripe_created_at=stripe_data["created_at"],
        ghl_contact_id=ghl_contact.get("id") if ghl_contact else None,
        ghl_email=ghl_attribution.get("email"),
        ghl_phone=ghl_attribution.get("phone"),
        ghl_name=(
            f"{ghl_attribution.get('first_name', '')} {ghl_attribution.get('last_name', '')}".strip()
            if ghl_contact else None
        ),
        ghl_fbclid=ghl_attribution.get("fbclid"),
        ghl_fbp=ghl_attribution.get("fbp"),
        ghl_utm_source=ghl_attribution.get("utm_source"),
        ghl_utm_medium=ghl_attribution.get("utm_medium"),
        ghl_utm_campaign=ghl_attribution.get("utm_campaign"),
        match_method=match_result["match_method"],
        match_score=match_result.get("match_score"),
        match_candidates=match_result.get("match_candidates"),
        capi_event_id=event_id,
        capi_status="pending",
        source=source,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    # Send to Meta CAPI (gated by credentials)
    dataset_id = settings.META_CAPI_DATASET_ID
    capi_token = settings.META_CAPI_ACCESS_TOKEN

    if not dataset_id or not capi_token:
        record.capi_status = "skipped"
        record.capi_error = "META_CAPI_DATASET_ID or META_CAPI_ACCESS_TOKEN not configured"
        db.commit()
        return {"status": "skipped", "id": record.id, "match": match_result["match_method"]}

    try:
        response = await send_to_meta_capi(event, dataset_id, capi_token)
        record.capi_status = "sent"
        record.capi_sent_at = datetime.now(timezone.utc)
        record.capi_response = response
        db.commit()
        logger.info(
            f"CAPI sent: {event_id} | match={match_result['match_method']} "
            f"| ${stripe_data['amount_cents']/100:.2f} "
            f"| fbclid={'yes' if ghl_attribution.get('fbclid') else 'no'}"
        )
        return {"status": "sent", "id": record.id, "match": match_result["match_method"]}

    except Exception as e:
        record.capi_status = "failed"
        record.capi_error = str(e)
        db.commit()
        logger.error(f"CAPI failed for {session_id}: {e}")
        return {"status": "failed", "id": record.id, "error": str(e)}


def _extract_stripe_data(session: dict) -> dict:
    """Normalize a Stripe checkout session or payment dict into a common shape."""
    customer_details = session.get("customer_details") or {}
    created = session.get("created") or session.get("stripe_created_at")
    if isinstance(created, (int, float)):
        created_dt = datetime.utcfromtimestamp(created)
    elif isinstance(created, datetime):
        created_dt = created
    else:
        created_dt = datetime.utcnow()

    return {
        "session_id": session.get("id") or session.get("session_id") or str(uuid.uuid4()),
        "customer_id": session.get("customer") or session.get("stripe_customer_id"),
        "email": (
            customer_details.get("email")
            or session.get("customer_email")
            or session.get("customer_email")
            or session.get("email")
            or ""
        ).lower().strip(),
        "phone": normalize_phone(
            customer_details.get("phone") or session.get("phone") or ""
        ),
        "name": (
            customer_details.get("name") or session.get("customer_name") or ""
        ).strip(),
        "amount_cents": session.get("amount_total") or session.get("amount_cents") or 0,
        "currency": (session.get("currency") or "usd").lower(),
        "created_at": created_dt,
    }
