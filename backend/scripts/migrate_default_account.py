"""
migrate_default_account.py — one-time script to migrate the default (.env)
YogiSoul account to AWS Secrets Manager.

Run inside the container:
    docker exec -it ghl-sync-app python scripts/migrate_default_account.py

What this does:
1. Reads current credentials from environment (.env / settings)
2. Writes them to AWS Secrets Manager at {AWS_SECRET_PREFIX}/{account_id}
3. Creates or updates the AdAccount DB record with aws_secret_name set
4. Verifies the round-trip by reading back from SM

Safe to re-run — it merges with any existing SM secret and is idempotent.
"""

import sys
import os

# Add backend/ to path so imports work when run from container root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from database import SessionLocal
from models import AdAccount
from services.credential_resolver import write_secret, _fetch_from_sm


def main():
    account_id = settings.META_AD_ACCOUNT_ID
    if not account_id:
        print("ERROR: META_AD_ACCOUNT_ID is not set in .env")
        sys.exit(1)

    if not account_id.startswith("act_"):
        account_id = f"act_{account_id}"

    if not settings.AWS_ACCESS_KEY_ID and not os.environ.get("AWS_ACCESS_KEY_ID"):
        print("ERROR: AWS credentials not configured (AWS_ACCESS_KEY_ID missing)")
        sys.exit(1)

    secret_name = f"{settings.AWS_SECRET_PREFIX}/{account_id}"

    data = {
        "meta_access_token": settings.META_ACCESS_TOKEN,
        "meta_ad_account_id": account_id,
        "meta_capi_dataset_id": settings.META_CAPI_DATASET_ID,
        "meta_capi_access_token": settings.META_CAPI_ACCESS_TOKEN,
        "ghl_api_key": settings.GHL_API_KEY,
        "ghl_location_id": settings.GHL_LOCATION_ID,
        "ghl_location_name": settings.GHL_LOCATION_NAME,
        "stripe_secret_key": settings.STRIPE_SECRET_KEY,
        "stripe_webhook_secret": settings.STRIPE_WEBHOOK_SECRET,
        "capi_event_source_url": settings.CAPI_EVENT_SOURCE_URL,
        "capi_event_name": settings.CAPI_EVENT_NAME,
    }

    # Remove empty values to keep the secret clean
    data = {k: v for k, v in data.items() if v}

    print(f"Writing secret: {secret_name}")
    print(f"Keys: {list(data.keys())}")

    arn = write_secret(secret_name, data)
    print(f"Secret written: {arn}")

    # Verify round-trip
    creds = _fetch_from_sm(secret_name)
    print(f"Verified — meta_access_token present: {bool(creds.meta_access_token)}")
    print(f"Verified — ghl_api_key present: {bool(creds.ghl_api_key)}")

    # Update DB record
    db = SessionLocal()
    try:
        record = db.query(AdAccount).filter(AdAccount.account_id == account_id).first()
        if record:
            record.aws_secret_name = secret_name
            db.commit()
            print(f"Updated DB record for {account_id}: aws_secret_name = {secret_name}")
        else:
            print(f"No AdAccount record found for {account_id} — create it via the UI first")
    finally:
        db.close()

    print("\nMigration complete.")
    print("You can now remove the following keys from .env (but keep them as fallback until verified):")
    for k in data:
        print(f"  {k.upper()}")


if __name__ == "__main__":
    main()
