"""
Stripe transaction history pull and LTV recomputation.
All Stripe API calls are gated by STRIPE_SECRET_KEY being set.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.ghl_client import get_all_contacts
from config import settings
from models import ContactLtv, StripeTransaction
from services.identity_resolver import match_stripe_to_ghl, normalize_phone

logger = logging.getLogger(__name__)


# ── LTV recomputation ────────────────────────────────────────────────────────

async def recompute_all_ltv(db: Session) -> int:
    """
    Recompute contact_ltv for every GHL contact with at least one matched
    succeeded transaction. Returns count of contacts updated.
    """
    rows = db.execute(text("""
        SELECT
            ghl_contact_id,
            SUM(amount_cents)        AS total_cents,
            SUM(refunded_amount)     AS total_refund_cents,
            COUNT(*)                 AS txn_count,
            MIN(stripe_created_at)   AS first_purchase,
            MAX(stripe_created_at)   AS last_purchase,
            json_agg(json_build_object(
                'name',   product_name,
                'amount', amount_cents / 100.0,
                'date',   stripe_created_at
            )) AS products
        FROM stripe_transactions
        WHERE ghl_contact_id IS NOT NULL
          AND status = 'succeeded'
        GROUP BY ghl_contact_id
    """)).fetchall()

    contacts_map = {c["id"]: c for c in await get_all_contacts()}
    updated = 0

    for row in rows:
        ghl_id = row.ghl_contact_id
        total_rev = (row.total_cents or 0) / 100.0
        total_refunds = (row.total_refund_cents or 0) / 100.0
        net = total_rev - total_refunds
        txn_count = row.txn_count or 0
        first = row.first_purchase
        last = row.last_purchase
        days = (last - first).days if first and last else 0
        frequency = round((txn_count / max(days, 1)) * 30, 2) if days > 0 else 0.0
        aov = round(net / txn_count, 2) if txn_count else 0.0

        # Aggregate products
        product_summary: dict = {}
        for p in (row.products or []):
            name = (p.get("name") or "Unknown").strip() or "Unknown"
            if name not in product_summary:
                product_summary[name] = {"name": name, "count": 0, "total": 0.0}
            product_summary[name]["count"] += 1
            product_summary[name]["total"] += float(p.get("amount") or 0)

        contact = contacts_map.get(ghl_id, {})
        ghl_name = (
            f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()
            if contact else ""
        )
        ghl_email = contact.get("email", "") if contact else ""

        existing = db.query(ContactLtv).filter_by(ghl_contact_id=ghl_id).first()
        if existing:
            existing.ghl_name = ghl_name
            existing.ghl_email = ghl_email
            existing.total_revenue = total_rev
            existing.total_refunds = total_refunds
            existing.net_revenue = net
            existing.transaction_count = txn_count
            existing.first_purchase_at = first
            existing.last_purchase_at = last
            existing.avg_order_value = aov
            existing.products_purchased = list(product_summary.values())
            existing.days_as_customer = days
            existing.purchase_frequency = frequency
            existing.updated_at = datetime.now(timezone.utc)
        else:
            db.add(ContactLtv(
                ghl_contact_id=ghl_id,
                ghl_name=ghl_name,
                ghl_email=ghl_email,
                total_revenue=total_rev,
                total_refunds=total_refunds,
                net_revenue=net,
                transaction_count=txn_count,
                first_purchase_at=first,
                last_purchase_at=last,
                avg_order_value=aov,
                products_purchased=list(product_summary.values()),
                days_as_customer=days,
                purchase_frequency=frequency,
            ))

        db.commit()
        updated += 1

    logger.info(f"LTV recomputed for {updated} contacts")
    return updated


# ── Stripe transaction sync ──────────────────────────────────────────────────

async def run_transaction_sync(
    db: Session,
    days_back: int | None = None,
    limit: int = 5000,
) -> dict:
    """
    Pull all Stripe PaymentIntents + orphan Charges, store in stripe_transactions,
    match each to a GHL contact, then recompute LTV.
    Requires STRIPE_SECRET_KEY.
    """
    if not settings.STRIPE_SECRET_KEY:
        return {"status": "skipped", "reason": "STRIPE_SECRET_KEY not configured"}

    import stripe as stripe_lib
    stripe_lib.api_key = settings.STRIPE_SECRET_KEY

    since_ts = None
    if days_back:
        since_ts = int((datetime.utcnow() - timedelta(days=days_back)).timestamp())

    # -- Pull PaymentIntents --
    all_payments: list[dict] = []
    seen_charge_ids: set[str] = set()
    params: dict = {"limit": 100}
    if since_ts:
        params["created"] = {"gte": since_ts}

    has_more = True
    starting_after = None
    while has_more and len(all_payments) < limit:
        p = dict(params)
        if starting_after:
            p["starting_after"] = starting_after
        batch = stripe_lib.PaymentIntent.list(**p, expand=["data.latest_charge", "data.customer"])
        for pi in batch.data:
            if pi.status != "succeeded":
                continue
            normalized = _normalize_payment_intent(pi)
            all_payments.append(normalized)
            if normalized.get("charge_id"):
                seen_charge_ids.add(normalized["charge_id"])
        has_more = batch.has_more
        if batch.data:
            starting_after = batch.data[-1].id

    # -- Pull orphan Charges (no linked PaymentIntent) --
    charge_params: dict = {"limit": 100}
    if since_ts:
        charge_params["created"] = {"gte": since_ts}
    has_more = True
    starting_after = None
    while has_more and len(all_payments) < limit:
        p = dict(charge_params)
        if starting_after:
            p["starting_after"] = starting_after
        batch = stripe_lib.Charge.list(**p)
        for charge in batch.data:
            if charge.status != "succeeded":
                continue
            if charge.id in seen_charge_ids:
                continue
            all_payments.append(_normalize_charge(charge))
        has_more = batch.has_more
        if batch.data:
            starting_after = batch.data[-1].id

    # -- Store and match --
    contacts = await get_all_contacts()
    stats = {"total": len(all_payments), "new": 0, "matched": 0, "skipped": 0}

    for payment in all_payments:
        existing = db.query(StripeTransaction).filter_by(
            stripe_payment_id=payment["payment_id"]
        ).first()
        if existing:
            stats["skipped"] += 1
            continue

        # Fetch line items for checkout sessions
        line_items: list = []
        product_name = None
        product_id = None
        price_id = None
        if payment.get("session_id"):
            try:
                items = stripe_lib.checkout.Session.list_line_items(payment["session_id"], limit=10)
                line_items = [item.to_dict() for item in items.data]
                if line_items:
                    first = line_items[0]
                    product_name = first.get("description", "")
                    price_data = first.get("price") or {}
                    if isinstance(price_data, dict):
                        product_id = price_data.get("product")
                        price_id = price_data.get("id")
            except Exception as e:
                logger.warning(f"Could not fetch line items for {payment['session_id']}: {e}")

        if not product_name:
            product_name = (
                (payment.get("metadata") or {}).get("product_name")
                or payment.get("description")
                or ""
            )

        match_result = await match_stripe_to_ghl(
            {
                "customer_id": payment.get("customer_id"),
                "email": payment.get("email", ""),
                "phone": payment.get("phone", ""),
                "name": payment.get("name", ""),
            },
            contacts,
            db,
        )
        ghl_contact = match_result["ghl_contact"]

        txn = StripeTransaction(
            stripe_payment_id=payment["payment_id"],
            stripe_customer_id=payment.get("customer_id"),
            stripe_session_id=payment.get("session_id"),
            stripe_invoice_id=payment.get("invoice_id"),
            customer_email=payment.get("email"),
            customer_phone=payment.get("phone"),
            customer_name=payment.get("name"),
            amount_cents=payment["amount_cents"],
            currency=payment["currency"],
            status="succeeded",
            payment_method=payment.get("payment_method_type"),
            stripe_created_at=payment["created_at"],
            line_items=line_items,
            product_name=product_name or None,
            product_id=product_id,
            price_id=price_id,
            quantity=line_items[0].get("quantity", 1) if line_items else 1,
            stripe_metadata=payment.get("metadata") or {},
            ghl_contact_id=ghl_contact.get("id") if ghl_contact else None,
            match_method=match_result["match_method"],
            match_status="matched" if ghl_contact else "unmatched",
        )
        db.add(txn)
        try:
            db.commit()
            stats["new"] += 1
            if ghl_contact:
                stats["matched"] += 1
        except Exception as e:
            db.rollback()
            logger.warning(f"Could not store transaction {payment['payment_id']}: {e}")

    stats["ltv_updated"] = await recompute_all_ltv(db)
    stats["status"] = "completed"
    return stats


# ── CAPI backfill (send historical conversions to Meta) ──────────────────────

async def run_capi_backfill(
    db: Session,
    days_back: int = 90,
    limit: int = 500,
    dry_run: bool = False,
    retry_failed: bool = False,
) -> dict:
    """
    Walk stripe_transactions and send to Meta CAPI.
    - Events ≤7 days old:  action_source="website"
    - Events 8–90 days:    action_source="physical_store" (Meta offline signals)
    - Events >90 days:     skipped (Meta hard limit)
    Requires META_CAPI_DATASET_ID + META_CAPI_ACCESS_TOKEN.
    """
    from services.conversion_tracker import build_capi_event, extract_ghl_attribution, send_to_meta_capi
    from models import MatchedConversion

    dataset_id = settings.META_CAPI_DATASET_ID
    capi_token = settings.META_CAPI_ACCESS_TOKEN
    if not dataset_id or not capi_token:
        return {"status": "skipped", "reason": "CAPI credentials not configured"}

    now = datetime.utcnow()
    cutoff = now - timedelta(days=min(days_back, 90))  # Meta hard limit ~90 days
    too_old_cutoff = now - timedelta(days=90)

    txns = (
        db.query(StripeTransaction)
        .filter(
            StripeTransaction.status == "succeeded",
            StripeTransaction.stripe_created_at >= cutoff,
        )
        .order_by(StripeTransaction.stripe_created_at.desc())
        .limit(limit)
        .all()
    )

    contacts = await get_all_contacts()
    contacts_map = {c["id"]: c for c in contacts}

    stats = {"total": len(txns), "sent": 0, "failed": 0, "skipped": 0, "too_old": 0, "dry_run": dry_run}

    for txn in txns:
        session_key = txn.stripe_session_id or txn.stripe_payment_id

        # Handle existing records
        existing = db.query(MatchedConversion).filter_by(stripe_session_id=session_key).first()
        if existing:
            if existing.capi_status == "sent":
                stats["skipped"] += 1
                continue
            if existing.capi_status == "failed" and not retry_failed:
                stats["skipped"] += 1
                continue
            # Retry failed record in place
            record = existing
        else:
            record = None

        # Determine action_source based on event age
        event_age_days = (now - txn.stripe_created_at).days
        if event_age_days > 90:
            stats["too_old"] += 1
            continue
        elif event_age_days > 7:
            action_source = "physical_store"
        else:
            action_source = "website"

        stripe_data = {
            "session_id": session_key,
            "customer_id": txn.stripe_customer_id,
            "email": txn.customer_email or "",
            "phone": txn.customer_phone or "",
            "name": txn.customer_name or "",
            "amount_cents": txn.amount_cents,
            "currency": txn.currency,
            "created_at": txn.stripe_created_at,
        }

        ghl_contact = contacts_map.get(txn.ghl_contact_id) if txn.ghl_contact_id else None
        ghl_attribution = extract_ghl_attribution(ghl_contact) if ghl_contact else {}

        event, event_id = build_capi_event(stripe_data, ghl_attribution, action_source)

        if record is None:
            record = MatchedConversion(
                stripe_session_id=session_key,
                stripe_customer_id=txn.stripe_customer_id,
                stripe_email=txn.customer_email,
                stripe_phone=txn.customer_phone,
                stripe_name=txn.customer_name,
                amount_cents=txn.amount_cents,
                currency=txn.currency,
                stripe_created_at=txn.stripe_created_at,
                ghl_contact_id=txn.ghl_contact_id,
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
                match_method=txn.match_method or "none",
                source="backfill",
            )
            db.add(record)

        record.capi_event_id = event_id
        record.capi_status = "pending"
        record.capi_error = None
        db.commit()
        db.refresh(record)

        if dry_run:
            record.capi_status = "skipped"
            record.capi_error = "dry_run"
            db.commit()
            stats["skipped"] += 1
            continue

        try:
            response = await send_to_meta_capi(event, dataset_id, capi_token)
            record.capi_status = "sent"
            record.capi_sent_at = datetime.now(timezone.utc)
            record.capi_response = response
            record.capi_error = None
            db.commit()
            stats["sent"] += 1
        except Exception as e:
            record.capi_status = "failed"
            record.capi_error = str(e)
            db.commit()
            stats["failed"] += 1
            logger.error(f"CAPI backfill failed for {session_key}: {e}")

    stats["status"] = "completed"
    return stats


# ── Stripe normalization helpers ─────────────────────────────────────────────

def _attr(obj, key, default=None):
    """Get a value from a Stripe object or dict safely."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize_payment_intent(pi) -> dict:
    charge = _attr(pi, "latest_charge")
    if isinstance(charge, str):
        charge = None  # not expanded — ignore
    bd = _attr(charge, "billing_details") if charge else None

    customer = _attr(pi, "customer")
    # customer may be a string ID, an expanded object, or None
    customer_id = customer if isinstance(customer, str) else _attr(customer, "id")
    customer_email = _attr(customer, "email") if not isinstance(customer, str) else None
    customer_phone = _attr(customer, "phone") if not isinstance(customer, str) else None
    customer_name = _attr(customer, "name") if not isinstance(customer, str) else None

    metadata = _attr(pi, "metadata") or {}
    if not isinstance(metadata, dict):
        try:
            metadata = {k: v for k, v in metadata.items()}
        except Exception:
            metadata = {}

    return {
        "payment_id": pi.id,
        "charge_id": _attr(charge, "id"),
        "customer_id": customer_id,
        "session_id": metadata.get("checkout_session"),
        "invoice_id": pi.invoice if isinstance(_attr(pi, "invoice"), str) else None,
        "email": (
            _attr(bd, "email") or customer_email or ""
        ),
        "phone": normalize_phone(
            _attr(bd, "phone") or customer_phone or ""
        ),
        "name": (
            _attr(bd, "name") or customer_name or ""
        ),
        "amount_cents": _attr(pi, "amount_received") or _attr(pi, "amount") or 0,
        "currency": _attr(pi, "currency") or "usd",
        "payment_method_type": (
            pi.payment_method_types[0] if _attr(pi, "payment_method_types") else None
        ),
        "created_at": datetime.utcfromtimestamp(_attr(pi, "created")),
        "metadata": metadata,
        "description": _attr(pi, "description") or "",
    }


def _normalize_charge(charge) -> dict:
    bd = charge.billing_details if hasattr(charge, "billing_details") else None
    return {
        "payment_id": charge.id,
        "charge_id": charge.id,
        "customer_id": charge.customer if isinstance(charge.customer, str) else None,
        "session_id": None,
        "invoice_id": charge.invoice if isinstance(charge.invoice, str) else None,
        "email": (bd.email if bd and hasattr(bd, "email") else None) or "",
        "phone": normalize_phone((bd.phone if bd and hasattr(bd, "phone") else None) or ""),
        "name": (bd.name if bd and hasattr(bd, "name") else None) or "",
        "amount_cents": charge.amount,
        "currency": charge.currency,
        "payment_method_type": (
            charge.payment_method_details.type
            if hasattr(charge, "payment_method_details") and charge.payment_method_details
            else None
        ),
        "created_at": datetime.utcfromtimestamp(charge.created),
        "metadata": {k: v for k, v in charge.metadata.items()} if charge.metadata else {},
        "description": charge.description or "",
    }
