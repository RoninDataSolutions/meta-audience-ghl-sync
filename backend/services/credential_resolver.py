"""
CredentialResolver — fetches per-account credentials from AWS Secrets Manager.

Each account has one SM secret at:
    {settings.AWS_SECRET_PREFIX}/{account_id}   e.g. /ghl-sync/accounts/89313216

The secret is a JSON object with all per-account keys (see AccountCredentials).
Falls back to settings (env) when SM is not configured or the account has no
aws_secret_name set — which covers the default YogiSoul account during and
after migration.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Credentials dataclass ────────────────────────────────────────────────────

@dataclass
class AccountCredentials:
    # Meta
    meta_access_token: str = ""
    meta_ad_account_id: str = ""      # normalised WITH act_ prefix
    meta_capi_dataset_id: str = ""
    meta_capi_access_token: str = ""
    # GHL
    ghl_api_key: str = ""
    ghl_location_id: str = ""
    ghl_location_name: str = ""
    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # CAPI settings
    capi_event_source_url: str = ""
    capi_event_name: str = "Purchase"

    def has_meta(self) -> bool:
        return bool(self.meta_access_token and self.meta_ad_account_id)

    def has_ghl(self) -> bool:
        return bool(self.ghl_api_key and self.ghl_location_id)

    def has_capi(self) -> bool:
        return bool(self.meta_capi_dataset_id and self.meta_capi_access_token)

    def has_stripe(self) -> bool:
        return bool(self.stripe_secret_key)

    def status(self) -> dict[str, bool]:
        return {
            "meta": self.has_meta(),
            "ghl": self.has_ghl(),
            "capi": self.has_capi(),
            "stripe": self.has_stripe(),
        }


# ── SM client (lazy init) ─────────────────────────────────────────────────────

_sm_client = None

def _get_sm_client():
    global _sm_client
    if _sm_client is None:
        from config import settings
        import boto3
        kwargs: dict = {"region_name": settings.AWS_REGION}
        if settings.AWS_ACCESS_KEY_ID:
            kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
        _sm_client = boto3.client("secretsmanager", **kwargs)
    return _sm_client


# ── TTL cache (60s) — avoids per-request SM calls ─────────────────────────────

_cache: dict[str, tuple[float, AccountCredentials]] = {}
_CACHE_TTL = 60


def _cached(secret_name: str) -> Optional[AccountCredentials]:
    if secret_name in _cache:
        ts, creds = _cache[secret_name]
        if time.time() - ts < _CACHE_TTL:
            return creds
        del _cache[secret_name]
    return None


def _store(secret_name: str, creds: AccountCredentials):
    _cache[secret_name] = (time.time(), creds)


def invalidate(secret_name: str):
    """Call after writing new credentials so the next read is fresh."""
    _cache.pop(secret_name, None)


# ── Core fetch ────────────────────────────────────────────────────────────────

def _fetch_from_sm(secret_name: str) -> AccountCredentials:
    cached = _cached(secret_name)
    if cached:
        return cached

    try:
        client = _get_sm_client()
        resp = client.get_secret_value(SecretId=secret_name)
        data = json.loads(resp["SecretString"])
        raw_id = data.get("meta_ad_account_id", "")
        if raw_id and not raw_id.startswith("act_"):
            raw_id = f"act_{raw_id}"
        creds = AccountCredentials(
            meta_access_token=data.get("meta_access_token", ""),
            meta_ad_account_id=raw_id,
            meta_capi_dataset_id=data.get("meta_capi_dataset_id", ""),
            meta_capi_access_token=data.get("meta_capi_access_token", ""),
            ghl_api_key=data.get("ghl_api_key", ""),
            ghl_location_id=data.get("ghl_location_id", ""),
            ghl_location_name=data.get("ghl_location_name", ""),
            stripe_secret_key=data.get("stripe_secret_key", ""),
            stripe_webhook_secret=data.get("stripe_webhook_secret", ""),
            capi_event_source_url=data.get("capi_event_source_url", ""),
            capi_event_name=data.get("capi_event_name", "Purchase"),
        )
        _store(secret_name, creds)
        return creds
    except Exception as e:
        logger.error(f"Failed to fetch SM secret '{secret_name}': {e}")
        raise


def _fallback_from_env() -> AccountCredentials:
    """Return credentials from env settings — used for the default account."""
    from config import settings
    raw_id = settings.META_AD_ACCOUNT_ID
    if raw_id and not raw_id.startswith("act_"):
        raw_id = f"act_{raw_id}"
    return AccountCredentials(
        meta_access_token=settings.META_ACCESS_TOKEN,
        meta_ad_account_id=raw_id,
        meta_capi_dataset_id=settings.META_CAPI_DATASET_ID,
        meta_capi_access_token=settings.META_CAPI_ACCESS_TOKEN,
        ghl_api_key=settings.GHL_API_KEY,
        ghl_location_id=settings.GHL_LOCATION_ID,
        ghl_location_name=settings.GHL_LOCATION_NAME,
        stripe_secret_key=settings.STRIPE_SECRET_KEY,
        stripe_webhook_secret=settings.STRIPE_WEBHOOK_SECRET,
        capi_event_source_url=settings.CAPI_EVENT_SOURCE_URL,
        capi_event_name=settings.CAPI_EVENT_NAME,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def resolve(account_id: Optional[str] = None, db=None) -> AccountCredentials:
    """
    Return credentials for the given account_id.

    Resolution order:
    1. If account has aws_secret_name in DB → fetch from SM
    2. Else → fall back to env (covers default account + pre-migration state)

    db is optional — if None, skips the DB lookup and goes straight to env.
    """
    if not account_id or not db:
        return _fallback_from_env()

    # Normalise
    lookup_id = account_id if account_id.startswith("act_") else f"act_{account_id}"

    try:
        from models import AdAccount
        record = db.query(AdAccount).filter(AdAccount.account_id == lookup_id).first()
    except Exception:
        record = None

    if record and record.aws_secret_name:
        try:
            return _fetch_from_sm(record.aws_secret_name)
        except Exception:
            logger.warning(f"SM fetch failed for {lookup_id}, falling back to env")
            return _fallback_from_env()

    return _fallback_from_env()


def write_secret(secret_name: str, data: dict) -> str:
    """
    Create or update a SM secret. Returns the secret ARN.
    data must match the AccountCredentials JSON shape.
    """
    client = _get_sm_client()
    secret_str = json.dumps(data)
    try:
        resp = client.put_secret_value(SecretId=secret_name, SecretString=secret_str)
        invalidate(secret_name)
        return resp["ARN"]
    except client.exceptions.ResourceNotFoundException:
        resp = client.create_secret(Name=secret_name, SecretString=secret_str)
        invalidate(secret_name)
        return resp["ARN"]


def secret_key_status(secret_name: str) -> dict[str, bool]:
    """Return which credential keys are present (non-empty) in a SM secret."""
    try:
        creds = _fetch_from_sm(secret_name)
        return {
            "meta_access_token": bool(creds.meta_access_token),
            "meta_ad_account_id": bool(creds.meta_ad_account_id),
            "meta_capi_dataset_id": bool(creds.meta_capi_dataset_id),
            "meta_capi_access_token": bool(creds.meta_capi_access_token),
            "ghl_api_key": bool(creds.ghl_api_key),
            "ghl_location_id": bool(creds.ghl_location_id),
            "ghl_location_name": bool(creds.ghl_location_name),
            "stripe_secret_key": bool(creds.stripe_secret_key),
            "stripe_webhook_secret": bool(creds.stripe_webhook_secret),
            "capi_event_source_url": bool(creds.capi_event_source_url),
        }
    except Exception:
        return {k: False for k in [
            "meta_access_token", "meta_ad_account_id", "meta_capi_dataset_id",
            "meta_capi_access_token", "ghl_api_key", "ghl_location_id",
            "ghl_location_name", "stripe_secret_key", "stripe_webhook_secret",
            "capi_event_source_url",
        ]}
