from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import SyncConfig
from api import ghl_client

router = APIRouter()


class ConfigPayload(BaseModel):
    ghl_ltv_field_key: str
    ghl_ltv_field_name: str
    meta_audience_id: str | None = None
    meta_lookalike_id: str | None = None


class ConfigResponse(BaseModel):
    id: int
    ghl_ltv_field_key: str
    ghl_ltv_field_name: str
    meta_ad_account_id: str
    meta_audience_id: str | None
    meta_lookalike_id: str | None
    sync_enabled: bool

    class Config:
        from_attributes = True


@router.get("/custom-fields")
async def get_custom_fields():
    try:
        fields = await ghl_client.get_custom_fields()
        return {"customFields": fields}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GHL API error: {str(e)}")


@router.get("/config")
def get_config(account_id: str | None = None, db: Session = Depends(get_db)):
    effective_account_id = account_id or settings.META_AD_ACCOUNT_ID
    q = db.query(SyncConfig).filter(
        SyncConfig.meta_ad_account_id == effective_account_id
    ).order_by(SyncConfig.id.desc())
    config = q.first()
    if not config:
        return {
            "config": None,
            "meta_ad_account_id": effective_account_id,
            "ghl_location_name": settings.GHL_LOCATION_NAME,
            "smtp_from": settings.SMTP_FROM_EMAIL,
            "smtp_to": settings.SMTP_TO_EMAIL,
        }
    return {
        "config": ConfigResponse.model_validate(config),
        "meta_ad_account_id": effective_account_id,
        "ghl_location_name": settings.GHL_LOCATION_NAME,
        "smtp_from": settings.SMTP_FROM_EMAIL,
        "smtp_to": settings.SMTP_TO_EMAIL,
    }


@router.post("/config")
def save_config(payload: ConfigPayload, account_id: str | None = None, db: Session = Depends(get_db)):
    effective_account_id = account_id or settings.META_AD_ACCOUNT_ID
    config = (
        db.query(SyncConfig)
        .filter(SyncConfig.meta_ad_account_id == effective_account_id)
        .order_by(SyncConfig.id.desc())
        .first()
    )
    if config:
        config.ghl_ltv_field_key = payload.ghl_ltv_field_key
        config.ghl_ltv_field_name = payload.ghl_ltv_field_name
        config.meta_audience_id = payload.meta_audience_id or None
        config.meta_lookalike_id = payload.meta_lookalike_id or None
    else:
        config = SyncConfig(
            ghl_ltv_field_key=payload.ghl_ltv_field_key,
            ghl_ltv_field_name=payload.ghl_ltv_field_name,
            meta_ad_account_id=effective_account_id,
            meta_audience_id=payload.meta_audience_id or None,
            meta_lookalike_id=payload.meta_lookalike_id or None,
            sync_enabled=True,
        )
        db.add(config)

    db.commit()
    db.refresh(config)
    return {"config": ConfigResponse.model_validate(config)}
