"""
Standalone Heat Map generation — runs only the geographic_breakdown logic
without the full Meta audit pipeline (no Claude analysis, no creative metadata,
no demographic/placement breakdowns). Much faster than triggering a full audit.

Useful when the user just wants the geographic overlay refreshed on demand.
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import AdAccount

logger = logging.getLogger(__name__)
router = APIRouter()


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

    return {
        "account_id": normalized,
        "days": days,
        "since": since,
        "until": until,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "geographic_breakdown": breakdown,
    }
