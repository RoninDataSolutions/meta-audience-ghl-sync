"""
Standalone Heat Map generation — runs only the geographic_breakdown logic
without the full Meta audit pipeline (no Claude analysis, no creative metadata,
no demographic/placement breakdowns). Much faster than triggering a full audit.

Useful when the user just wants the geographic overlay refreshed on demand.
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc

from config import settings
from database import get_db
from models import AdAccount, HeatmapSnapshot

logger = logging.getLogger(__name__)
router = APIRouter()


def _save_snapshot(
    db: Session,
    account_id: str,
    account_name: str | None,
    days: int,
    since: str,
    until: str,
    breakdown: dict,
    source: str,
) -> int | None:
    """Persist a heat map snapshot. Returns the new snapshot id, or None on failure."""
    try:
        summary = breakdown.get("summary") or {}
        narrative = breakdown.get("narrative") or {}
        reall = narrative.get("reallocation") or {}
        snap = HeatmapSnapshot(
            account_id=account_id,
            account_name=account_name,
            days_back=days,
            since=since,
            until=until,
            source=source,
            geographic_breakdown=breakdown,
            total_spend=summary.get("total_spend"),
            total_ltv=summary.get("total_ltv"),
            ltv_roas=summary.get("ltv_roas"),
            projected_revenue_gain=reall.get("projected_revenue_gain"),
            states_with_spend=summary.get("states_with_spend"),
            states_with_paying=summary.get("states_with_ltv"),
        )
        db.add(snap)
        db.commit()
        db.refresh(snap)
        logger.info(f"Saved HeatmapSnapshot id={snap.id} for {account_id} ({days}d, source={source})")
        return snap.id
    except Exception as e:
        logger.warning(f"Failed to save HeatmapSnapshot: {e}", exc_info=True)
        db.rollback()
        return None


@router.post("/heatmap/generate")
async def generate_heatmap(
    account_id: str | None = None,
    days: int = 30,
    db: Session = Depends(get_db),
):
    """
    Run only the geographic breakdown for the given account. Returns the
    full geographic_breakdown shape that the frontend renders.

    days: lookback window for both Meta ad spend AND matched Stripe conversions
          (clamped to 1..365). LTV from GHL contacts is always lifetime,
          independent of window.
    """
    from services.credential_resolver import resolve
    from services.geographic_breakdown import build_geographic_breakdown
    from api.ghl_client import get_all_contacts

    # Clamp days to a sane range
    days = max(1, min(int(days), 365))

    # Normalize account id
    if account_id:
        normalized = account_id if account_id.startswith("act_") else f"act_{account_id}"
    else:
        normalized = (
            settings.META_AD_ACCOUNT_ID
            if settings.META_AD_ACCOUNT_ID.startswith("act_")
            else f"act_{settings.META_AD_ACCOUNT_ID}"
        )

    creds = resolve(normalized, db)
    token = creds.meta_access_token or settings.META_ACCESS_TOKEN
    if not token:
        raise HTTPException(status_code=400, detail="No Meta access token available")

    if not (creds.has_ghl() or (settings.GHL_API_KEY and settings.GHL_LOCATION_ID)):
        raise HTTPException(
            status_code=400,
            detail="GHL is not configured for this account — heat map needs contact data",
        )

    today = datetime.utcnow().date()
    since = (today - timedelta(days=days)).isoformat()
    until = today.isoformat()

    try:
        contacts = await get_all_contacts(creds=creds)
    except Exception as e:
        logger.error(f"Heat map: GHL contact fetch failed: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch GHL contacts: {e}")

    try:
        breakdown = await build_geographic_breakdown(
            account_id=normalized,
            token=token,
            since=since,
            until=until,
            contacts=contacts,
            db=db,
            creds=creds,
        )
    except Exception as e:
        logger.error(f"Heat map: build_geographic_breakdown failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Heat map generation failed: {e}")

    # Resolve account display name for the snapshot
    record = db.query(AdAccount).filter(AdAccount.account_id == normalized).first()
    account_name = record.account_name if record else normalized
    snapshot_id = _save_snapshot(db, normalized, account_name, days, since, until, breakdown, source="api")

    return {
        "account_id": normalized,
        "days": days,
        "since": since,
        "until": until,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "snapshot_id": snapshot_id,
        "geographic_breakdown": breakdown,
    }


@router.get("/heatmap/pdf")
async def heatmap_pdf(
    account_id: str | None = None,
    days: int = 30,
    db: Session = Depends(get_db),
):
    """
    Generate the heat map dashboard as a server-side PDF (ReportLab).
    Streams `application/pdf` so the browser downloads it.
    """
    from services.credential_resolver import resolve
    from services.geographic_breakdown import build_geographic_breakdown
    from services.heatmap_pdf import generate_heatmap_pdf
    from api.ghl_client import get_all_contacts

    days = max(1, min(int(days), 365))

    if account_id:
        normalized = account_id if account_id.startswith("act_") else f"act_{account_id}"
    else:
        normalized = (
            settings.META_AD_ACCOUNT_ID
            if settings.META_AD_ACCOUNT_ID.startswith("act_")
            else f"act_{settings.META_AD_ACCOUNT_ID}"
        )

    creds = resolve(normalized, db)
    token = creds.meta_access_token or settings.META_ACCESS_TOKEN
    if not token:
        raise HTTPException(status_code=400, detail="No Meta access token available")

    if not (creds.has_ghl() or (settings.GHL_API_KEY and settings.GHL_LOCATION_ID)):
        raise HTTPException(
            status_code=400,
            detail="GHL is not configured for this account — heat map needs contact data",
        )

    # Resolve account display name
    record = db.query(AdAccount).filter(AdAccount.account_id == normalized).first()
    account_name = record.account_name if record else normalized

    today = datetime.utcnow().date()
    since = (today - timedelta(days=days)).isoformat()
    until = today.isoformat()

    try:
        contacts = await get_all_contacts(creds=creds)
    except Exception as e:
        logger.error(f"PDF: GHL contact fetch failed: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch GHL contacts: {e}")

    try:
        breakdown = await build_geographic_breakdown(
            account_id=normalized, token=token,
            since=since, until=until,
            contacts=contacts, db=db, creds=creds,
        )
    except Exception as e:
        logger.error(f"PDF: geographic breakdown failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Heat map build failed: {e}")

    # Persist the snapshot before rendering — even if PDF generation fails,
    # we keep the data.
    _save_snapshot(db, normalized, account_name, days, since, until, breakdown, source="pdf")

    try:
        pdf_bytes = generate_heatmap_pdf(breakdown, account_name=account_name, days=days)
    except Exception as e:
        logger.error(f"PDF: rendering failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"PDF rendering failed: {e}")

    safe_name = account_name.replace(" ", "_").replace("/", "_")
    filename = f"geo_roas_{safe_name}_{days}d_{today.isoformat()}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _num(v):
    """Cast Numeric/Decimal columns to float for JSON serialization."""
    return float(v) if v is not None else None


@router.get("/heatmap/snapshots")
async def list_snapshots(
    account_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """
    List stored heat map snapshots, newest first. Returns lightweight rows
    (denormalized summary columns only, no JSON payload) for the history view.
    """
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    q = db.query(HeatmapSnapshot)
    if account_id:
        normalized = account_id if account_id.startswith("act_") else f"act_{account_id}"
        q = q.filter(HeatmapSnapshot.account_id == normalized)

    total = q.count()
    rows = q.order_by(desc(HeatmapSnapshot.generated_at)).offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "snapshots": [
            {
                "id": r.id,
                "account_id": r.account_id,
                "account_name": r.account_name,
                "generated_at": r.generated_at.isoformat() + "Z" if r.generated_at else None,
                "days_back": r.days_back,
                "since": r.since,
                "until": r.until,
                "source": r.source,
                "total_spend": _num(r.total_spend),
                "total_ltv": _num(r.total_ltv),
                "ltv_roas": _num(r.ltv_roas),
                "projected_revenue_gain": _num(r.projected_revenue_gain),
                "states_with_spend": r.states_with_spend,
                "states_with_paying": r.states_with_paying,
            }
            for r in rows
        ],
    }


@router.get("/heatmap/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: int, db: Session = Depends(get_db)):
    """Fetch a single snapshot with its full geographic_breakdown payload."""
    r = db.query(HeatmapSnapshot).filter(HeatmapSnapshot.id == snapshot_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {
        "id": r.id,
        "account_id": r.account_id,
        "account_name": r.account_name,
        "generated_at": r.generated_at.isoformat() + "Z" if r.generated_at else None,
        "days_back": r.days_back,
        "since": r.since,
        "until": r.until,
        "source": r.source,
        "total_spend": _num(r.total_spend),
        "total_ltv": _num(r.total_ltv),
        "ltv_roas": _num(r.ltv_roas),
        "projected_revenue_gain": _num(r.projected_revenue_gain),
        "states_with_spend": r.states_with_spend,
        "states_with_paying": r.states_with_paying,
        "geographic_breakdown": r.geographic_breakdown,
    }


@router.delete("/heatmap/snapshots/{snapshot_id}")
async def delete_snapshot(snapshot_id: int, db: Session = Depends(get_db)):
    """Delete a stored snapshot."""
    r = db.query(HeatmapSnapshot).filter(HeatmapSnapshot.id == snapshot_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    db.delete(r)
    db.commit()
    return {"status": "deleted", "id": snapshot_id}
