import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import AdAccount

logger = logging.getLogger(__name__)
router = APIRouter()

BASE_META_URL = "https://graph.facebook.com/v21.0"


class BusinessProfile(BaseModel):
    industry: str | None = None
    description: str | None = None
    target_customer: str | None = None
    avg_order_value: float | None = None
    primary_goal: str | None = None
    facebook_page_id: str | None = None
    competitor_page_ids: str | None = None  # comma-separated page IDs


class AccountCreate(BaseModel):
    account_id: str
    account_name: str
    meta_access_token: str | None = None
    notification_email: str | None = None
    audit_cron: str | None = None
    website_url: str | None = None
    business_profile: BusinessProfile | None = None
    business_notes: str | None = None


class AccountUpdate(BaseModel):
    account_name: str | None = None
    meta_access_token: str | None = None
    notification_email: str | None = None
    audit_cron: str | None = None
    is_active: bool | None = None
    website_url: str | None = None
    business_profile: BusinessProfile | None = None
    business_notes: str | None = None


def _account_to_dict(account: AdAccount) -> dict:
    return {
        "id": account.id,
        "account_id": account.account_id,
        "account_name": account.account_name,
        "has_custom_token": bool(account.meta_access_token),
        "notification_email": account.notification_email,
        "audit_cron": account.audit_cron,
        "is_active": account.is_active,
        "last_audit_at": account.last_audit_at.isoformat() if account.last_audit_at else None,
        "currency": account.currency,
        "timezone_name": account.timezone_name,
        "website_url": account.website_url,
        "business_profile": account.business_profile or {},
        "business_notes": account.business_notes,
        "created_at": account.created_at.isoformat() if account.created_at else None,
    }


def _normalize_account_id(account_id: str) -> str:
    if not account_id.startswith("act_"):
        return f"act_{account_id}"
    return account_id


async def _test_meta_token(account_id: str, token: str) -> dict:
    """Call Meta to validate token and return account info."""
    account_id = _normalize_account_id(account_id)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{BASE_META_URL}/{account_id}",
            params={"access_token": token, "fields": "name,currency,timezone_name,account_status"},
        )
        resp.raise_for_status()
        return resp.json()


@router.post("/accounts")
async def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    account_id = _normalize_account_id(payload.account_id)

    existing = db.query(AdAccount).filter(AdAccount.account_id == account_id).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Account {account_id} already exists")

    token = payload.meta_access_token or settings.META_ACCESS_TOKEN
    if not token:
        raise HTTPException(status_code=400, detail="No Meta access token available (set one or configure META_ACCESS_TOKEN in .env)")

    try:
        meta_info = await _test_meta_token(account_id, token)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=400, detail=f"Meta API rejected token: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not validate token: {e}")

    account = AdAccount(
        account_id=account_id,
        account_name=payload.account_name or meta_info.get("name", account_id),
        meta_access_token=payload.meta_access_token,
        notification_email=payload.notification_email,
        audit_cron=payload.audit_cron,
        currency=meta_info.get("currency"),
        timezone_name=meta_info.get("timezone_name"),
        website_url=payload.website_url,
        business_profile=payload.business_profile.model_dump(exclude_none=True) if payload.business_profile else {},
        business_notes=payload.business_notes,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    logger.info(f"Added ad account: {account_id}")
    return _account_to_dict(account)


@router.get("/accounts")
def list_accounts(db: Session = Depends(get_db)):
    accounts = db.query(AdAccount).order_by(AdAccount.created_at.desc()).all()
    return {"accounts": [_account_to_dict(a) for a in accounts]}


@router.put("/accounts/{account_db_id}")
def update_account(account_db_id: int, payload: AccountUpdate, db: Session = Depends(get_db)):
    account = db.query(AdAccount).filter(AdAccount.id == account_db_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if payload.account_name is not None:
        account.account_name = payload.account_name
    # Only update token if explicitly provided and non-empty
    if payload.meta_access_token:
        account.meta_access_token = payload.meta_access_token
    if payload.notification_email is not None:
        account.notification_email = payload.notification_email
    if payload.audit_cron is not None:
        account.audit_cron = payload.audit_cron or None
    if payload.is_active is not None:
        account.is_active = payload.is_active
    if payload.website_url is not None:
        account.website_url = payload.website_url or None
    if payload.business_profile is not None:
        account.business_profile = payload.business_profile.model_dump(exclude_none=True)
    if payload.business_notes is not None:
        account.business_notes = payload.business_notes or None

    db.commit()
    db.refresh(account)
    return _account_to_dict(account)


@router.delete("/accounts/{account_db_id}")
def deactivate_account(account_db_id: int, db: Session = Depends(get_db)):
    account = db.query(AdAccount).filter(AdAccount.id == account_db_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    account.is_active = False
    db.commit()
    return {"status": "deactivated", "account_id": account.account_id}


@router.post("/accounts/{account_db_id}/test")
async def test_account_token(account_db_id: int, db: Session = Depends(get_db)):
    account = db.query(AdAccount).filter(AdAccount.id == account_db_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    token = account.meta_access_token or settings.META_ACCESS_TOKEN
    try:
        meta_info = await _test_meta_token(account.account_id, token)
        # Update currency/timezone if changed
        account.currency = meta_info.get("currency", account.currency)
        account.timezone_name = meta_info.get("timezone_name", account.timezone_name)
        db.commit()
        return {
            "status": "ok",
            "account_name": meta_info.get("name"),
            "currency": meta_info.get("currency"),
            "timezone_name": meta_info.get("timezone_name"),
            "token_source": "custom" if account.meta_access_token else "default",
        }
    except httpx.HTTPStatusError as e:
        return {"status": "error", "detail": e.response.text}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
