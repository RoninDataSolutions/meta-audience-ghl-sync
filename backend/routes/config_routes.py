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


class ConfigResponse(BaseModel):
    id: int
    ghl_ltv_field_key: str
    ghl_ltv_field_name: str
    meta_ad_account_id: str
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
def get_config(db: Session = Depends(get_db)):
    config = db.query(SyncConfig).order_by(SyncConfig.id.desc()).first()
    if not config:
        return {
            "config": None,
            "meta_ad_account_id": settings.META_AD_ACCOUNT_ID,
            "ghl_location_name": settings.GHL_LOCATION_NAME,
            "smtp_from": settings.SMTP_FROM_EMAIL,
            "smtp_to": settings.SMTP_TO_EMAIL,
        }
    return {
        "config": ConfigResponse.model_validate(config),
        "meta_ad_account_id": settings.META_AD_ACCOUNT_ID,
        "ghl_location_name": settings.GHL_LOCATION_NAME,
        "smtp_from": settings.SMTP_FROM_EMAIL,
        "smtp_to": settings.SMTP_TO_EMAIL,
    }


@router.post("/config")
def save_config(payload: ConfigPayload, db: Session = Depends(get_db)):
    config = db.query(SyncConfig).order_by(SyncConfig.id.desc()).first()
    if config:
        config.ghl_ltv_field_key = payload.ghl_ltv_field_key
        config.ghl_ltv_field_name = payload.ghl_ltv_field_name
        config.meta_ad_account_id = settings.META_AD_ACCOUNT_ID
    else:
        config = SyncConfig(
            ghl_ltv_field_key=payload.ghl_ltv_field_key,
            ghl_ltv_field_name=payload.ghl_ltv_field_name,
            meta_ad_account_id=settings.META_AD_ACCOUNT_ID,
            sync_enabled=True,
        )
        db.add(config)

    db.commit()
    db.refresh(config)
    return {"config": ConfigResponse.model_validate(config)}
