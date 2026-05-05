import asyncio
import logging
import random
from typing import Any, TYPE_CHECKING

import httpx

from config import settings

if TYPE_CHECKING:
    from services.credential_resolver import AccountCredentials

logger = logging.getLogger(__name__)

BASE_URL = "https://graph.facebook.com/v21.0"
BATCH_SIZE = 10_000
MAX_RETRIES = 3
RETRY_DELAYS = [2, 4, 8]


def _token(creds: "AccountCredentials | None") -> str:
    return creds.meta_access_token if creds else settings.META_ACCESS_TOKEN


def _ad_account(creds: "AccountCredentials | None") -> str:
    raw = creds.meta_ad_account_id if creds else settings.META_AD_ACCOUNT_ID
    return raw if raw.startswith("act_") else f"act_{raw}"


async def _request(method: str, url: str, **kwargs) -> dict:
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.request(method.upper(), url, **kwargs)
                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.warning(f"Meta rate limited, retrying in {delay}s (attempt {attempt + 1})")
                    await asyncio.sleep(delay)
                    continue
                if resp.status_code >= 400:
                    logger.error(f"Meta API error {resp.status_code}: {resp.text}")
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError:
            raise
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning(f"Meta API error: {e}, retrying in {delay}s")
                await asyncio.sleep(delay)
            else:
                raise
    raise RuntimeError("Max retries exceeded for Meta API request")


async def audience_exists(
    audience_id: str,
    creds: "AccountCredentials | None" = None,
) -> bool:
    try:
        await _request(
            "get",
            f"{BASE_URL}/{audience_id}",
            params={"access_token": _token(creds), "fields": "id"},
        )
        return True
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (400, 404):
            return False
        raise


async def create_custom_audience(
    name: str,
    description: str,
    creds: "AccountCredentials | None" = None,
) -> dict:
    acct = _ad_account(creds)
    result = await _request(
        "post",
        f"{BASE_URL}/{acct}/customaudiences",
        params={"access_token": _token(creds)},
        json={
            "name": name,
            "subtype": "CUSTOM",
            "description": description,
            "customer_file_source": "USER_PROVIDED_ONLY",
            "is_value_based": True,
        },
    )
    audience_id = result.get("id")
    logger.info(f"Created Meta Custom Audience: {name} (ID: {audience_id})")
    return {"id": audience_id, "name": name}


async def upload_users(
    audience_id: str,
    schema: list[str],
    data: list[list[Any]],
    creds: "AccountCredentials | None" = None,
) -> dict:
    total_received = 0
    total_invalid = 0
    total_batches = max((len(data) + BATCH_SIZE - 1) // BATCH_SIZE, 1)
    session_id = random.randint(1, 2**32)

    for i in range(0, max(len(data), 1), BATCH_SIZE):
        batch = data[i:i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        is_last = batch_num == total_batches
        logger.info(f"Uploading batch {batch_num}/{total_batches} ({len(batch)} contacts)")

        result = await _request(
            "post",
            f"{BASE_URL}/{audience_id}/users",
            params={"access_token": _token(creds)},
            json={
                "payload": {"schema": schema, "data": batch},
                "session": {
                    "session_id": session_id,
                    "batch_seq": batch_num,
                    "last_batch_flag": is_last,
                    "estimated_num_total": len(data),
                },
            },
        )
        total_received += result.get("num_received", len(batch))
        total_invalid += result.get("num_invalid_entries", 0)

    logger.info(f"Upload complete: {total_received} received, {total_invalid} invalid")
    return {"num_received": total_received, "num_invalid": total_invalid}


async def find_lookalike_for_source(
    origin_audience_id: str,
    creds: "AccountCredentials | None" = None,
) -> dict | None:
    acct = _ad_account(creds)
    try:
        result = await _request(
            "get",
            f"{BASE_URL}/{acct}/customaudiences",
            params={
                "access_token": _token(creds),
                "fields": "id,name,subtype,lookalike_spec",
                "limit": 100,
            },
        )
        for audience in result.get("data", []):
            spec = audience.get("lookalike_spec") or {}
            origin_objects = spec.get("origin") or []
            origin_ids_from_objects = [o.get("id", "") for o in origin_objects if isinstance(o, dict)]
            legacy_ids = spec.get("origin_audience_id") or spec.get("origin_ids") or []
            if isinstance(legacy_ids, str):
                legacy_ids = [legacy_ids]
            origins = origin_ids_from_objects + legacy_ids
            if origin_audience_id in origins:
                logger.info(f"Found existing lookalike {audience['id']} for source {origin_audience_id}")
                return {"id": audience["id"], "name": audience.get("name", "")}
    except Exception as e:
        logger.warning(f"Could not search for existing lookalike: {e}")
    return None


async def create_lookalike_audience(
    origin_audience_id: str,
    name: str,
    creds: "AccountCredentials | None" = None,
) -> dict:
    acct = _ad_account(creds)
    try:
        result = await _request(
            "post",
            f"{BASE_URL}/{acct}/customaudiences",
            params={"access_token": _token(creds)},
            json={
                "name": name,
                "subtype": "LOOKALIKE",
                "origin_audience_id": origin_audience_id,
                "lookalike_spec": {"type": "custom_ratio", "ratio": 0.01, "country": "US"},
            },
        )
        lookalike_id = result.get("id")
        logger.info(f"Created Lookalike Audience: {name} (ID: {lookalike_id})")
        return {"id": lookalike_id, "name": name}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            try:
                body = e.response.json()
                if body.get("error", {}).get("code") == 2654:
                    logger.warning("Duplicate lookalike detected — searching for existing one")
                    existing = await find_lookalike_for_source(origin_audience_id, creds)
                    if existing:
                        return existing
            except Exception:
                pass
        raise
