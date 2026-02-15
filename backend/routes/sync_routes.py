import asyncio
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from models import SyncConfig, SyncRun, SyncContact, SyncStatus
from services import sync_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _run_to_dict(run: SyncRun) -> dict[str, Any]:
    duration = None
    if run.started_at and run.completed_at:
        duration = (run.completed_at - run.started_at).total_seconds()

    return {
        "id": run.id,
        "config_id": run.config_id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "status": run.status,
        "contacts_processed": run.contacts_processed,
        "contacts_matched": run.contacts_matched,
        "meta_audience_id": run.meta_audience_id,
        "meta_audience_name": run.meta_audience_name,
        "meta_lookalike_id": run.meta_lookalike_id,
        "meta_lookalike_name": run.meta_lookalike_name,
        "error_message": run.error_message,
        "normalization_stats": run.normalization_stats,
        "duration_seconds": duration,
    }


async def _run_sync_background(config_id: int):
    """Run sync in background with its own DB session."""
    db = SessionLocal()
    try:
        await sync_service.run_sync(config_id, db)
    finally:
        db.close()


@router.post("/sync/trigger")
async def trigger_sync(db: Session = Depends(get_db)):
    if sync_service.is_sync_running():
        raise HTTPException(status_code=409, detail="A sync is already running")

    config = db.query(SyncConfig).order_by(SyncConfig.id.desc()).first()
    if not config:
        raise HTTPException(status_code=400, detail="No sync configuration found. Please configure first.")

    # Run in background
    asyncio.create_task(_run_sync_background(config.id))
    return {"message": "Sync triggered", "config_id": config.id}


@router.get("/sync/status")
def get_sync_status(db: Session = Depends(get_db)):
    running_id = sync_service.get_running_sync_id()
    last_run = db.query(SyncRun).order_by(SyncRun.id.desc()).first()

    return {
        "is_running": running_id is not None,
        "running_sync_id": running_id,
        "last_run": _run_to_dict(last_run) if last_run else None,
    }


@router.get("/sync/history")
def get_sync_history(page: int = 1, per_page: int = 20, db: Session = Depends(get_db)):
    total = db.query(SyncRun).count()
    runs = (
        db.query(SyncRun)
        .order_by(SyncRun.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return {
        "runs": [_run_to_dict(r) for r in runs],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total > 0 else 0,
    }


@router.get("/sync/{sync_id}")
def get_sync_detail(sync_id: int, db: Session = Depends(get_db)):
    run = db.query(SyncRun).filter(SyncRun.id == sync_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Sync run not found")

    # Get sample contacts (first 10)
    contacts = (
        db.query(SyncContact)
        .filter(SyncContact.sync_run_id == sync_id)
        .limit(10)
        .all()
    )

    contact_samples = [
        {
            "ghl_contact_id": c.ghl_contact_id,
            "email": c.email,
            "first_name": c.first_name,
            "last_name": c.last_name,
            "raw_ltv": float(c.raw_ltv) if c.raw_ltv else 0,
            "normalized_value": c.normalized_value,
        }
        for c in contacts
    ]

    result = _run_to_dict(run)
    result["contact_samples"] = contact_samples
    return result
