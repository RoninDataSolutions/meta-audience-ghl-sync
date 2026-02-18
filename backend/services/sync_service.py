import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from api import ghl_client, meta_client
from models import SyncConfig, SyncRun, SyncContact, SyncStatus
from services.hasher import prepare_contact_row
from services.normalizer import normalize_and_stats
from services import email_service

logger = logging.getLogger(__name__)

# Track if a sync is currently running
_running_sync_id: int | None = None


def is_sync_running() -> bool:
    return _running_sync_id is not None


def get_running_sync_id() -> int | None:
    return _running_sync_id


async def run_sync(config_id: int, db: Session) -> None:
    """Execute the full sync workflow."""
    global _running_sync_id

    # Create sync run record
    run = SyncRun(config_id=config_id, status=SyncStatus.RUNNING)
    db.add(run)
    db.commit()
    db.refresh(run)
    _running_sync_id = run.id

    try:
        config = db.query(SyncConfig).filter(SyncConfig.id == config_id).first()
        if not config:
            raise ValueError(f"Config {config_id} not found")

        logger.info(f"Starting sync run {run.id}, LTV field: {config.ghl_ltv_field_name}")

        # Step 1: Fetch all contacts from GHL
        logger.info("Step 1: Fetching all contacts from GHL...")
        contacts = await ghl_client.get_all_contacts()
        if not contacts:
            raise ValueError("No contacts found in GHL location")

        # Step 2: Extract LTV values, filter to contacts with valid LTV
        logger.info("Step 2: Extracting LTV values...")
        # GHL v2 contacts store custom fields by UUID id, not fieldKey.
        # Resolve the configured fieldKey (e.g. "contact.ltv") to its UUID.
        custom_fields = await ghl_client.get_custom_fields()
        ltv_field_uuid = None
        for cf in custom_fields:
            if cf.get("fieldKey") == config.ghl_ltv_field_key or cf.get("id") == config.ghl_ltv_field_key:
                ltv_field_uuid = cf.get("id")
                break
        if not ltv_field_uuid:
            raise ValueError(
                f"Could not resolve LTV field '{config.ghl_ltv_field_key}' to a GHL custom field ID. "
                f"Available fields: {[f.get('fieldKey') for f in custom_fields]}"
            )
        logger.info(f"Resolved LTV field '{config.ghl_ltv_field_key}' → UUID '{ltv_field_uuid}'")
        ltv_values = []
        valid_contacts = []
        for c in contacts:
            raw_ltv = _extract_ltv(c, ltv_field_uuid)
            if raw_ltv is not None and raw_ltv > 0:
                ltv_values.append(raw_ltv)
                valid_contacts.append(c)

        if not ltv_values:
            logger.warning(
                f"No contacts with valid LTV values found in field '{config.ghl_ltv_field_name}'. "
                f"Total contacts fetched: {len(contacts)}. Stopping sync early."
            )
            run.status = SyncStatus.WARNING
            run.completed_at = datetime.now(timezone.utc)
            run.contacts_processed = 0
            run.contacts_matched = 0
            run.error_message = f"No LTV values found in field '{config.ghl_ltv_field_name}' across {len(contacts)} contacts"
            db.commit()
            return

        run.contacts_processed = len(valid_contacts)
        db.commit()
        logger.info(f"Found {len(valid_contacts)} contacts with LTV values (out of {len(contacts)} total)")

        # Step 3: Normalize via Claude
        logger.info("Step 3: Normalizing LTV values via Claude API...")
        percentiles, norm_stats = normalize_and_stats(ltv_values)

        # Step 4: Hash PII and prepare rows
        logger.info("Step 4: Preparing contact data (hashing PII)...")
        schema = ["EMAIL", "PHONE", "FN", "LN", "CT", "ST", "ZIP", "COUNTRY", "LOOKALIKE_VALUE"]
        rows = [
            prepare_contact_row(contact, pct)
            for contact, pct in zip(valid_contacts, percentiles)
        ]

        # Step 5: Get or create Meta Custom Audience
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        audience_name = "GHL-HighValue"

        # Reuse existing audience from last successful run if available
        last_success = (
            db.query(SyncRun)
            .filter(SyncRun.config_id == config_id, SyncRun.meta_audience_id.isnot(None))
            .order_by(SyncRun.id.desc())
            .first()
        )

        if last_success and last_success.meta_audience_id:
            logger.info(f"Step 5: Reusing existing Meta Audience ID {last_success.meta_audience_id}, clearing old users...")
            await meta_client.delete_all_users(last_success.meta_audience_id)
            audience = {"id": last_success.meta_audience_id, "name": audience_name}
        else:
            logger.info(f"Step 5: Creating new Meta Custom Audience: {audience_name}")
            audience = await meta_client.create_custom_audience(
                name=audience_name,
                description=f"GHL high-value contacts synced via LTV normalization",
            )

        # Step 6: Upload contacts in batches
        logger.info("Step 6: Uploading contacts to Meta...")
        upload_result = await meta_client.upload_users(audience["id"], schema, rows)

        # Step 7: Get or create Lookalike Audience
        lookalike_name = f"{audience_name}-LAL-1%"
        last_lookalike_id = last_success.meta_lookalike_id if last_success else None
        if last_lookalike_id:
            logger.info(f"Step 7: Reusing existing Lookalike Audience ID {last_lookalike_id}")
            lookalike = {"id": last_lookalike_id, "name": lookalike_name}
        else:
            logger.info(f"Step 7: Creating Lookalike Audience: {lookalike_name}")
            lookalike = await meta_client.create_lookalike_audience(
                origin_audience_id=audience["id"],
                name=lookalike_name,
            )

        # Step 8: Update sync run record
        run.status = SyncStatus.SUCCESS
        run.completed_at = datetime.now(timezone.utc)
        run.contacts_processed = len(valid_contacts)
        run.contacts_matched = upload_result.get("num_received", 0)
        run.meta_audience_id = audience["id"]
        run.meta_audience_name = audience["name"]
        run.meta_lookalike_id = lookalike["id"]
        run.meta_lookalike_name = lookalike["name"]
        run.normalization_stats = norm_stats
        db.commit()

        # Step 9: Store contact details
        logger.info("Step 9: Storing contact details...")
        for contact, raw_ltv, pct in zip(valid_contacts, ltv_values, percentiles):
            sc = SyncContact(
                sync_run_id=run.id,
                ghl_contact_id=contact.get("id", ""),
                email=contact.get("email"),
                phone=contact.get("phone"),
                first_name=contact.get("firstName"),
                last_name=contact.get("lastName"),
                raw_ltv=Decimal(str(raw_ltv)),
                normalized_value=pct,
                meta_matched=True,
            )
            db.add(sc)
        db.commit()

        # Step 10: Send success email
        logger.info("Step 10: Sending success email...")
        try:
            email_service.send_success_email(run)
        except Exception as e:
            logger.error(f"Failed to send success email: {e}")

        logger.info(f"Sync run {run.id} completed successfully!")

    except Exception as e:
        logger.error(f"Sync run {run.id} failed: {e}", exc_info=True)
        run.status = SyncStatus.FAILED
        run.error_message = str(e)
        run.completed_at = datetime.now(timezone.utc)
        db.commit()

        try:
            email_service.send_failure_email(run, str(e))
        except Exception as email_err:
            logger.error(f"Failed to send failure email: {email_err}")

    finally:
        _running_sync_id = None


def _extract_ltv(contact: dict, field_uuid: str) -> float | None:
    """Extract LTV value from contact's customFields list by UUID."""
    for cf in contact.get("customFields", []):
        if cf.get("id") == field_uuid:
            try:
                return float(cf.get("value") or 0)
            except (ValueError, TypeError):
                return None
    return None
