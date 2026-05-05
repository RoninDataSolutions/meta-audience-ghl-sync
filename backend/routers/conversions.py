"""
Conversion tracking router — all endpoints for:
  - Stripe webhook (real-time)
  - CAPI backfill (historical)
  - Transaction sync (full Stripe history)
  - Conversion / transaction list views
  - Manual match management
  - LTV leaderboard
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal
from models import (
    ContactIdentityMap,
    ContactLtv,
    MatchedConversion,
    StripeTransaction,
)
from services.conversion_tracker import process_conversion
from services.transaction_sync import recompute_all_ltv, run_capi_backfill, run_transaction_sync

logger = logging.getLogger(__name__)
router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Pydantic models ──────────────────────────────────────────────────────────

class BackfillRequest(BaseModel):
    days_back: int = 90
    limit: int = 500
    dry_run: bool = False
    retry_failed: bool = True


class TransactionSyncRequest(BaseModel):
    days_back: Optional[int] = None
    limit: int = 5000


class ManualMatchRequest(BaseModel):
    ghl_contact_id: str


# ── Stripe webhook ───────────────────────────────────────────────────────────

@router.post("/webhooks/stripe-conversion")
async def stripe_conversion_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Real-time Stripe conversion webhook.
    Configure in Stripe Dashboard → Developers → Webhooks.
    Event: checkout.session.completed
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if settings.STRIPE_WEBHOOK_SECRET:
        try:
            import stripe as stripe_lib
            event = stripe_lib.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")

        if event["type"] != "checkout.session.completed":
            return {"status": "ignored", "type": event["type"]}

        session = event["data"]["object"].to_dict()
    else:
        # No webhook secret configured — accept raw JSON for testing
        import json
        body = json.loads(payload)
        event_type = body.get("type", "")
        if event_type and event_type != "checkout.session.completed":
            return {"status": "ignored", "type": event_type}
        session = body.get("data", {}).get("object", body)

    result = await process_conversion(session, db, source="webhook")
    return result


# ── CAPI backfill ────────────────────────────────────────────────────────────

@router.post("/conversions/backfill")
async def backfill_conversions(
    body: BackfillRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Send historical stripe_transactions to Meta CAPI."""
    background_tasks.add_task(
        _run_backfill_bg, body.days_back, body.limit, body.dry_run, body.retry_failed
    )
    return {"status": "started", "days_back": body.days_back, "dry_run": body.dry_run}


async def _run_backfill_bg(days_back: int, limit: int, dry_run: bool, retry_failed: bool):
    import traceback
    db = SessionLocal()
    try:
        result = await run_capi_backfill(db, days_back, limit, dry_run, retry_failed)
        logger.info(f"CAPI backfill complete: {result}")
    except Exception as e:
        logger.error(f"CAPI backfill error: {e}\n{traceback.format_exc()}")
    finally:
        db.close()


# ── Transaction sync ─────────────────────────────────────────────────────────

@router.post("/transactions/sync")
async def sync_stripe_transactions(
    body: TransactionSyncRequest,
    background_tasks: BackgroundTasks,
):
    """Pull full Stripe payment history and build the transaction ledger."""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=400, detail="STRIPE_SECRET_KEY not configured")
    background_tasks.add_task(_run_sync_bg, body.days_back, body.limit)
    return {"status": "started", "days_back": body.days_back}


async def _run_sync_bg(days_back: int | None, limit: int):
    import traceback
    db = SessionLocal()
    try:
        result = await run_transaction_sync(db, days_back, limit)
        logger.info(f"Transaction sync complete: {result}")
    except Exception as e:
        logger.error(f"Transaction sync error: {e}\n{traceback.format_exc()}")
    finally:
        db.close()


@router.post("/transactions/recompute-ltv")
async def recompute_ltv(db: Session = Depends(get_db)):
    """Recompute LTV for all matched contacts from stripe_transactions."""
    updated = await recompute_all_ltv(db)
    return {"status": "completed", "contacts_updated": updated}


# ── Transaction list ─────────────────────────────────────────────────────────

@router.get("/transactions")
def list_transactions(
    limit: int = 50,
    offset: int = 0,
    product_name: Optional[str] = None,
    match_status: Optional[str] = None,
    ghl_contact_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(StripeTransaction)
    if product_name:
        q = q.filter(StripeTransaction.product_name.ilike(f"%{product_name}%"))
    if match_status:
        q = q.filter(StripeTransaction.match_status == match_status)
    if ghl_contact_id:
        q = q.filter(StripeTransaction.ghl_contact_id == ghl_contact_id)
    if date_from:
        q = q.filter(StripeTransaction.stripe_created_at >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.filter(StripeTransaction.stripe_created_at <= datetime.fromisoformat(date_to))

    total = q.count()
    rows = q.order_by(desc(StripeTransaction.stripe_created_at)).offset(offset).limit(limit).all()

    # Summary stats
    all_succeeded = (
        db.query(
            func.sum(StripeTransaction.amount_cents).label("total_cents"),
            func.count(StripeTransaction.id).label("total"),
            func.count(func.distinct(StripeTransaction.customer_email)).label("unique_customers"),
        )
        .filter(StripeTransaction.status == "succeeded")
        .first()
    )
    total_cents = all_succeeded.total_cents or 0
    total_txns = all_succeeded.total or 0

    return {
        "transactions": [_txn_to_dict(t) for t in rows],
        "total": total,
        "summary": {
            "total_revenue": round(total_cents / 100, 2),
            "unique_customers": all_succeeded.unique_customers or 0,
            "match_rate": _match_rate(db),
            "avg_order_value": round(total_cents / total_txns / 100, 2) if total_txns else 0,
        },
    }


@router.get("/transactions/unmatched")
def list_unmatched_transactions(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    rows = (
        db.query(StripeTransaction)
        .filter(StripeTransaction.match_status == "unmatched")
        .order_by(desc(StripeTransaction.stripe_created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    total = (
        db.query(StripeTransaction)
        .filter(StripeTransaction.match_status == "unmatched")
        .count()
    )
    return {"transactions": [_txn_to_dict(t) for t in rows], "total": total}


@router.post("/transactions/{txn_id}/match")
def manually_match_transaction(
    txn_id: int,
    body: ManualMatchRequest,
    db: Session = Depends(get_db),
):
    """Manually link an unmatched transaction to a GHL contact."""
    txn = db.query(StripeTransaction).filter_by(id=txn_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    txn.ghl_contact_id = body.ghl_contact_id
    txn.match_method = "manual"
    txn.match_status = "matched"
    db.commit()

    # Cache in identity map
    email = (txn.customer_email or "").lower().strip() or None
    if txn.stripe_customer_id or email:
        existing = db.query(ContactIdentityMap).filter_by(
            stripe_email=email,
            ghl_contact_id=body.ghl_contact_id,
        ).first()
        if not existing:
            db.add(ContactIdentityMap(
                stripe_customer_id=txn.stripe_customer_id,
                stripe_email=email,
                ghl_contact_id=body.ghl_contact_id,
                match_method="manual",
                confirmed=True,
            ))
            db.commit()

    return {"status": "matched", "id": txn_id, "ghl_contact_id": body.ghl_contact_id}


# ── LTV leaderboard ──────────────────────────────────────────────────────────

@router.get("/transactions/ltv")
def ltv_leaderboard(
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "net_revenue",
    db: Session = Depends(get_db),
):
    sort_col = {
        "net_revenue": desc(ContactLtv.net_revenue),
        "transaction_count": desc(ContactLtv.transaction_count),
        "last_purchase_at": desc(ContactLtv.last_purchase_at),
    }.get(sort_by, desc(ContactLtv.net_revenue))

    total = db.query(ContactLtv).count()
    rows = db.query(ContactLtv).order_by(sort_col).offset(offset).limit(limit).all()

    # Aggregate summary
    agg = db.query(
        func.avg(ContactLtv.net_revenue).label("avg_ltv"),
        func.sum(ContactLtv.net_revenue).label("total_rev"),
        func.percentile_cont(0.5).within_group(ContactLtv.net_revenue).label("median_ltv"),
        func.percentile_cont(0.9).within_group(ContactLtv.net_revenue).label("p90_ltv"),
    ).first()

    return {
        "contacts": [_ltv_to_dict(r) for r in rows],
        "total": total,
        "summary": {
            "median_ltv": round(float(agg.median_ltv or 0), 2),
            "avg_ltv": round(float(agg.avg_ltv or 0), 2),
            "top_10_pct_ltv": round(float(agg.p90_ltv or 0), 2),
            "total_customer_revenue": round(float(agg.total_rev or 0), 2),
        },
    }


# ── Conversion list ──────────────────────────────────────────────────────────

@router.get("/conversions")
def list_conversions(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    match_method: Optional[str] = None,
    source: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(MatchedConversion)
    if status:
        q = q.filter(MatchedConversion.capi_status == status)
    if match_method:
        q = q.filter(MatchedConversion.match_method == match_method)
    if source:
        q = q.filter(MatchedConversion.source == source)

    total = q.count()
    rows = q.order_by(desc(MatchedConversion.created_at)).offset(offset).limit(limit).all()
    stats = _conversion_stats(db)

    return {
        "conversions": [_conversion_to_dict(c) for c in rows],
        "total": total,
        "stats": stats,
    }


@router.get("/conversions/stats")
def conversion_stats(db: Session = Depends(get_db)):
    return _conversion_stats(db)


@router.get("/conversions/unmatched")
def list_unmatched_conversions(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    rows = (
        db.query(MatchedConversion)
        .filter(MatchedConversion.match_method == "none")
        .order_by(desc(MatchedConversion.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    total = db.query(MatchedConversion).filter(MatchedConversion.match_method == "none").count()
    return {"conversions": [_conversion_to_dict(c) for c in rows], "total": total}


@router.get("/conversions/{conv_id}")
def get_conversion(conv_id: int, db: Session = Depends(get_db)):
    conv = db.query(MatchedConversion).filter_by(id=conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversion not found")
    return _conversion_to_dict(conv, full=True)


@router.post("/conversions/{conv_id}/retry")
async def retry_conversion(conv_id: int, db: Session = Depends(get_db)):
    """Retry a failed CAPI send."""
    from services.conversion_tracker import build_capi_event, send_to_meta_capi

    conv = db.query(MatchedConversion).filter_by(id=conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversion not found")
    if conv.capi_status == "sent":
        return {"status": "already_sent", "id": conv_id}

    dataset_id = settings.META_CAPI_DATASET_ID
    capi_token = settings.META_CAPI_ACCESS_TOKEN
    if not dataset_id or not capi_token:
        raise HTTPException(status_code=400, detail="CAPI credentials not configured")

    stripe_data = {
        "session_id": conv.stripe_session_id,
        "customer_id": conv.stripe_customer_id,
        "email": conv.stripe_email or "",
        "phone": conv.stripe_phone or "",
        "name": conv.stripe_name or "",
        "amount_cents": conv.amount_cents,
        "currency": conv.currency,
        "created_at": conv.stripe_created_at,
    }
    ghl_attribution = {
        "fbclid": conv.ghl_fbclid,
        "fbp": conv.ghl_fbp,
        "email": conv.ghl_email or "",
        "phone": conv.ghl_phone or "",
        "utm_source": conv.ghl_utm_source,
        "utm_medium": conv.ghl_utm_medium,
        "utm_campaign": conv.ghl_utm_campaign,
    }

    event, event_id = build_capi_event(stripe_data, ghl_attribution)
    try:
        response = await send_to_meta_capi(event, dataset_id, capi_token)
        conv.capi_status = "sent"
        conv.capi_sent_at = datetime.now(timezone.utc)
        conv.capi_response = response
        conv.capi_event_id = event_id
        conv.capi_error = None
        db.commit()
        return {"status": "sent", "id": conv_id}
    except Exception as e:
        conv.capi_status = "failed"
        conv.capi_error = str(e)
        db.commit()
        raise HTTPException(status_code=502, detail=str(e))


# ── Identity map management ──────────────────────────────────────────────────

@router.post("/conversions/identity-map/{map_id}/confirm")
def confirm_identity_match(map_id: int, db: Session = Depends(get_db)):
    """Mark a fuzzy match as manually confirmed."""
    row = db.query(ContactIdentityMap).filter_by(id=map_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Identity map entry not found")
    row.confirmed = True
    db.commit()
    return {"status": "confirmed", "id": map_id}


@router.delete("/conversions/identity-map/{map_id}")
def delete_identity_match(map_id: int, db: Session = Depends(get_db)):
    """Remove an incorrect match from the identity map."""
    row = db.query(ContactIdentityMap).filter_by(id=map_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Identity map entry not found")
    db.delete(row)
    db.commit()
    return {"status": "deleted", "id": map_id}


# ── Serializers ──────────────────────────────────────────────────────────────

def _txn_to_dict(t: StripeTransaction) -> dict:
    return {
        "id": t.id,
        "stripe_payment_id": t.stripe_payment_id,
        "stripe_session_id": t.stripe_session_id,
        "customer_email": t.customer_email,
        "customer_name": t.customer_name,
        "customer_phone": t.customer_phone,
        "amount": round((t.amount_cents or 0) / 100, 2),
        "currency": t.currency,
        "product_name": t.product_name,
        "ghl_contact_id": t.ghl_contact_id,
        "match_method": t.match_method,
        "match_status": t.match_status,
        "stripe_created_at": t.stripe_created_at.isoformat() if t.stripe_created_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _ltv_to_dict(r: ContactLtv) -> dict:
    return {
        "ghl_contact_id": r.ghl_contact_id,
        "ghl_name": r.ghl_name,
        "ghl_email": r.ghl_email,
        "net_revenue": float(r.net_revenue or 0),
        "total_revenue": float(r.total_revenue or 0),
        "total_refunds": float(r.total_refunds or 0),
        "transaction_count": r.transaction_count,
        "first_purchase_at": r.first_purchase_at.isoformat() if r.first_purchase_at else None,
        "last_purchase_at": r.last_purchase_at.isoformat() if r.last_purchase_at else None,
        "avg_order_value": float(r.avg_order_value or 0),
        "days_as_customer": r.days_as_customer,
        "purchase_frequency": float(r.purchase_frequency or 0),
        "products_purchased": r.products_purchased or [],
    }


def _conversion_to_dict(c: MatchedConversion, full: bool = False) -> dict:
    d = {
        "id": c.id,
        "stripe_session_id": c.stripe_session_id,
        "stripe_email": c.stripe_email,
        "stripe_name": c.stripe_name,
        "amount": round((c.amount_cents or 0) / 100, 2),
        "currency": c.currency,
        "ghl_name": c.ghl_name,
        "ghl_contact_id": c.ghl_contact_id,
        "match_method": c.match_method,
        "match_score": c.match_score,
        "has_fbclid": bool(c.ghl_fbclid),
        "capi_status": c.capi_status,
        "capi_sent_at": c.capi_sent_at.isoformat() if c.capi_sent_at else None,
        "source": c.source,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
    if full:
        d.update({
            "stripe_customer_id": c.stripe_customer_id,
            "stripe_phone": c.stripe_phone,
            "ghl_email": c.ghl_email,
            "ghl_phone": c.ghl_phone,
            "ghl_fbclid": c.ghl_fbclid,
            "ghl_fbp": c.ghl_fbp,
            "ghl_utm_source": c.ghl_utm_source,
            "ghl_utm_medium": c.ghl_utm_medium,
            "ghl_utm_campaign": c.ghl_utm_campaign,
            "match_candidates": c.match_candidates,
            "capi_event_id": c.capi_event_id,
            "capi_response": c.capi_response,
            "capi_error": c.capi_error,
        })
    return d


def _conversion_stats(db: Session) -> dict:
    total = db.query(MatchedConversion).count()
    sent = db.query(MatchedConversion).filter(MatchedConversion.capi_status == "sent").count()
    failed = db.query(MatchedConversion).filter(MatchedConversion.capi_status == "failed").count()
    has_fbclid = db.query(MatchedConversion).filter(
        MatchedConversion.ghl_fbclid.isnot(None)
    ).count()
    matched = db.query(MatchedConversion).filter(
        MatchedConversion.match_method != "none"
    ).count()
    revenue_agg = db.query(
        func.sum(MatchedConversion.amount_cents)
    ).filter(MatchedConversion.capi_status == "sent").scalar() or 0

    return {
        "total": total,
        "total_sent": sent,
        "total_failed": failed,
        "match_rate": round(matched / total * 100, 1) if total else 0,
        "fbclid_rate": round(has_fbclid / total * 100, 1) if total else 0,
        "capi_success_rate": round(sent / total * 100, 1) if total else 0,
        "total_revenue_tracked": round(revenue_agg / 100, 2),
    }


def _match_rate(db: Session) -> float:
    total = db.query(StripeTransaction).filter(StripeTransaction.status == "succeeded").count()
    matched = (
        db.query(StripeTransaction)
        .filter(
            StripeTransaction.status == "succeeded",
            StripeTransaction.ghl_contact_id.isnot(None),
        )
        .count()
    )
    return round(matched / total * 100, 1) if total else 0.0
