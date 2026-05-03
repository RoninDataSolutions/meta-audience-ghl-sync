import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from database import get_db, SessionLocal
from models import AdAccount, AuditReport

logger = logging.getLogger(__name__)
router = APIRouter()


def _fmt_contexts(contexts: list[dict]) -> str | None:
    parts = []
    for c in (contexts or []):
        text = (c.get("text") or "").strip()
        if not text:
            continue
        added_at = (c.get("added_at") or "")[:10]
        parts.append(f"[{added_at}] {text}" if added_at else text)
    return "\n\n".join(parts) or None


class AuditTriggerRequest(BaseModel):
    account_id: str | None = None
    models: list[str] = ["claude"]
    include_comparison: bool = True
    report_notes: str | None = None


def _report_to_dict(report: AuditReport, include_full: bool = False) -> dict:
    base = {
        "id": report.id,
        "account_id": report.account_id,
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "status": report.status,
        "total_spend_7d": float(report.total_spend_7d) if report.total_spend_7d is not None else None,
        "total_spend_30d": float(report.total_spend_30d) if report.total_spend_30d is not None else None,
        "total_conversions_7d": report.total_conversions_7d,
        "total_conversions_30d": report.total_conversions_30d,
        "total_impressions_7d": report.total_impressions_7d,
        "total_impressions_30d": report.total_impressions_30d,
        "total_clicks_7d": report.total_clicks_7d,
        "total_clicks_30d": report.total_clicks_30d,
        "avg_cpa_30d": float(report.avg_cpa_30d) if report.avg_cpa_30d is not None else None,
        "avg_ctr_30d": float(report.avg_ctr_30d) if report.avg_ctr_30d is not None else None,
        "avg_roas_30d": float(report.avg_roas_30d) if report.avg_roas_30d is not None else None,
        "campaign_count": report.campaign_count,
        "audience_count": report.audience_count,
        "models_used": report.models_used,
        "has_pdf": report.pdf_report is not None,
        "error_message": report.error_message,
    }
    if include_full:
        base["analyses"] = report.analyses or {}
        base["raw_metrics"] = report.raw_metrics or {}
        base["report_notes"] = report.report_notes
        base["audit_contexts"] = report.audit_contexts or []
    return base


def _resolve_account(account_id: str | None, db: Session) -> tuple[str, str]:
    """Return (normalized_account_id, token). Falls back to .env values."""
    if account_id:
        normalized = account_id if account_id.startswith("act_") else f"act_{account_id}"
        record = db.query(AdAccount).filter(AdAccount.account_id == normalized).first()
        if record:
            token = record.meta_access_token or settings.META_ACCESS_TOKEN
            return normalized, token
        # Not in DB — use default token
        return normalized, settings.META_ACCESS_TOKEN

    # Fall back to env
    env_id = settings.META_AD_ACCOUNT_ID
    if not env_id.startswith("act_"):
        env_id = f"act_{env_id}"
    return env_id, settings.META_ACCESS_TOKEN


async def _run_audit_background(
    report_id: int,
    account_id: str,
    token: str,
    models_to_run: list[str],
    business_profile: dict | None = None,
    website_url: str | None = None,
    business_notes: str | None = None,
    report_notes: str | None = None,
):
    db = SessionLocal()
    try:
        from services.meta_audit import run_audit
        await run_audit(
            report_id=report_id,
            account_id=account_id,
            token=token,
            db=db,
            models_to_run=models_to_run,
            business_profile=business_profile,
            website_url=website_url,
            business_notes=business_notes,
            report_notes=report_notes,
        )
    except Exception as e:
        logger.error(f"Background audit {report_id} failed: {e}", exc_info=True)
    finally:
        db.close()


@router.post("/audit/trigger")
async def trigger_audit(payload: AuditTriggerRequest, db: Session = Depends(get_db)):
    account_id, token = _resolve_account(payload.account_id, db)

    if not token:
        raise HTTPException(status_code=400, detail="No Meta access token configured")

    # Validate requested models
    valid_models = {"claude", "openai"}
    models_to_run = [m for m in payload.models if m in valid_models]
    if "claude" not in models_to_run:
        models_to_run = ["claude"] + models_to_run  # Claude is always required

    # Check OpenAI key if requested
    if "openai" in models_to_run and not settings.OPENAI_API_KEY:
        models_to_run = [m for m in models_to_run if m != "openai"]
        logger.warning("OpenAI model requested but OPENAI_API_KEY not set — skipping")

    # Look up account name for response
    account_record = db.query(AdAccount).filter(AdAccount.account_id == account_id).first()
    account_name = account_record.account_name if account_record else account_id

    initial_contexts = []
    if payload.report_notes and payload.report_notes.strip():
        initial_contexts = [{
            "text": payload.report_notes.strip(),
            "added_at": datetime.now(timezone.utc).isoformat(),
        }]

    report = AuditReport(
        account_id=account_id,
        status="in_progress",
        analyses={},
        models_used=",".join(models_to_run),
        report_notes=payload.report_notes or None,
        audit_contexts=initial_contexts or None,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    # Update last_audit_at on the account record
    if account_record:
        account_record.last_audit_at = datetime.now(timezone.utc)
        db.commit()

    business_profile = account_record.business_profile if account_record else None
    website_url = account_record.website_url if account_record else None
    business_notes = account_record.business_notes if account_record else None
    formatted_notes = _fmt_contexts(report.audit_contexts)
    asyncio.create_task(_run_audit_background(
        report.id, account_id, token, models_to_run,
        business_profile=business_profile,
        website_url=website_url,
        business_notes=business_notes,
        report_notes=formatted_notes,
    ))

    return {
        "status": "started",
        "report_id": report.id,
        "account_id": account_id,
        "account_name": account_name,
        "models": models_to_run,
        "message": f"Audit started. Poll /api/audit/reports/{report.id} for status.",
    }


@router.get("/audit/reports")
def list_reports(
    limit: int = 10,
    offset: int = 0,
    account_id: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(AuditReport)
    if account_id:
        normalized = account_id if account_id.startswith("act_") else f"act_{account_id}"
        query = query.filter(AuditReport.account_id == normalized)

    total = query.count()
    reports = query.order_by(AuditReport.id.desc()).offset(offset).limit(limit).all()

    # Enrich with account names
    account_ids = list({r.account_id for r in reports})
    account_map = {
        a.account_id: a.account_name
        for a in db.query(AdAccount).filter(AdAccount.account_id.in_(account_ids)).all()
    }

    result = []
    for r in reports:
        d = _report_to_dict(r)
        d["account_name"] = account_map.get(r.account_id, r.account_id)
        result.append(d)

    return {"reports": result, "total": total, "limit": limit, "offset": offset}


@router.get("/audit/reports/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(AuditReport).filter(AuditReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    result = _report_to_dict(report, include_full=True)

    account_record = db.query(AdAccount).filter(AdAccount.account_id == report.account_id).first()
    result["account_name"] = account_record.account_name if account_record else report.account_id

    # Compute comparison deltas vs previous completed report
    prev = (
        db.query(AuditReport)
        .filter(
            AuditReport.account_id == report.account_id,
            AuditReport.status == "completed",
            AuditReport.id < report_id,
        )
        .order_by(AuditReport.id.desc())
        .first()
    )
    if prev:
        def _delta(current, previous):
            if current is None or previous is None or previous == 0:
                return None
            c, p = float(current), float(previous)
            return {"previous": p, "current": c, "change_pct": round((c - p) / p * 100, 2)}

        result["comparison"] = {
            "previous_report_id": prev.id,
            "previous_generated_at": prev.generated_at.isoformat() if prev.generated_at else None,
            "deltas": {
                "spend_7d": _delta(report.total_spend_7d, prev.total_spend_7d),
                "spend_30d": _delta(report.total_spend_30d, prev.total_spend_30d),
                "conversions_7d": _delta(report.total_conversions_7d, prev.total_conversions_7d),
                "conversions_30d": _delta(report.total_conversions_30d, prev.total_conversions_30d),
                "impressions_30d": _delta(report.total_impressions_30d, prev.total_impressions_30d),
                "clicks_30d": _delta(report.total_clicks_30d, prev.total_clicks_30d),
                "cpa_30d": _delta(report.avg_cpa_30d, prev.avg_cpa_30d),
                "ctr_30d": _delta(report.avg_ctr_30d, prev.avg_ctr_30d),
                "roas_30d": _delta(report.avg_roas_30d, prev.avg_roas_30d),
            },
        }
    else:
        result["comparison"] = None

    return result


@router.get("/audit/reports/{report_id}/pdf")
def download_pdf(report_id: int, db: Session = Depends(get_db)):
    report = db.query(AuditReport).filter(AuditReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if not report.pdf_report:
        raise HTTPException(status_code=404, detail="PDF not available for this report")

    filename = report.pdf_filename or f"audit_{report.account_id}_{report_id}.pdf"
    return StreamingResponse(
        iter([report.pdf_report]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/audit/reports/{report_id}/json")
def download_json(report_id: int, db: Session = Depends(get_db)):
    import json
    report = db.query(AuditReport).filter(AuditReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    archive = {
        "report_id": report.id,
        "account_id": report.account_id,
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "raw_metrics": report.raw_metrics,
        "analyses": report.analyses,
    }
    filename = f"audit_{report.account_id}_{report_id}.json"
    content = json.dumps(archive, indent=2, default=str).encode()
    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/audit/reports/{report_id}/regenerate-pdf")
def regenerate_pdf(report_id: int, db: Session = Depends(get_db)):
    from services.audit_pdf import generate_pdf

    report = db.query(AuditReport).filter(AuditReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status != "completed":
        raise HTTPException(status_code=400, detail="Can only regenerate PDF for successful reports")

    account = db.query(AdAccount).filter(AdAccount.account_id == report.account_id).first()
    account_name = account.account_name if account else report.account_id

    metrics = {
        "total_spend_7d": report.total_spend_7d,
        "total_spend_30d": report.total_spend_30d,
        "total_conversions_7d": report.total_conversions_7d,
        "total_conversions_30d": report.total_conversions_30d,
        "total_impressions_7d": report.total_impressions_7d,
        "total_impressions_30d": report.total_impressions_30d,
        "total_clicks_7d": report.total_clicks_7d,
        "total_clicks_30d": report.total_clicks_30d,
        "avg_cpa_30d": report.avg_cpa_30d,
        "avg_ctr_30d": report.avg_ctr_30d,
        "avg_roas_30d": report.avg_roas_30d,
        "campaign_count": report.campaign_count,
        "audience_count": report.audience_count,
    }

    pdf_bytes = generate_pdf(
        account_name=account_name,
        metrics=metrics,
        raw_metrics=report.raw_metrics or {},
        analyses=report.analyses or {},
        prev_report=None,
    )

    report.pdf_report = pdf_bytes
    report.pdf_filename = f"audit_{report.account_id}_{report_id}.pdf"
    db.commit()

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report.pdf_filename}"'},
    )


class ReanalyzeRequest(BaseModel):
    models: list[str] = ["claude"]
    context_text: str | None = None


async def _reanalyze_background(report_id: int, models_to_run: list[str]):
    db = SessionLocal()
    try:
        from services.meta_audit import reanalyze_audit
        await reanalyze_audit(report_id=report_id, db=db, models_to_run=models_to_run)
    except Exception as e:
        logger.error(f"Background reanalysis {report_id} failed: {e}", exc_info=True)
    finally:
        db.close()


@router.post("/audit/reports/{report_id}/reanalyze")
async def reanalyze_report(
    report_id: int,
    payload: ReanalyzeRequest,
    db: Session = Depends(get_db),
):
    report = db.query(AuditReport).filter(AuditReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status == "in_progress":
        raise HTTPException(status_code=409, detail="Report is already being processed")
    if not report.raw_metrics:
        raise HTTPException(status_code=400, detail="No stored metrics to re-analyze")

    valid_models = {"claude", "openai"}
    models_to_run = [m for m in payload.models if m in valid_models]
    if "claude" not in models_to_run:
        models_to_run = ["claude"] + models_to_run

    if "openai" in models_to_run and not settings.OPENAI_API_KEY:
        models_to_run = [m for m in models_to_run if m != "openai"]

    if payload.context_text and payload.context_text.strip():
        now = datetime.now(timezone.utc).isoformat()
        contexts = list(report.audit_contexts or [])
        contexts.append({"text": payload.context_text.strip(), "added_at": now})
        report.audit_contexts = contexts

    report.status = "in_progress"
    report.error_message = None
    db.commit()

    asyncio.create_task(_reanalyze_background(report_id, models_to_run))

    return {
        "status": "started",
        "report_id": report_id,
        "models": models_to_run,
        "message": f"Re-analysis started. Poll /api/audit/reports/{report_id} for status.",
    }


class AddContextRequest(BaseModel):
    text: str


@router.post("/audit/reports/{report_id}/context")
def add_audit_context(report_id: int, payload: AddContextRequest, db: Session = Depends(get_db)):
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Context text cannot be empty")
    report = db.query(AuditReport).filter(AuditReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    now = datetime.now(timezone.utc).isoformat()
    contexts = list(report.audit_contexts or [])
    contexts.append({"text": payload.text.strip(), "added_at": now})
    report.audit_contexts = contexts
    db.commit()

    return {"audit_contexts": contexts}


@router.delete("/audit/reports/{report_id}")
def delete_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(AuditReport).filter(AuditReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    db.delete(report)
    db.commit()
    return {"status": "deleted", "report_id": report_id}
