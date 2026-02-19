import asyncio
import logging
import random
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://graph.facebook.com/v21.0"
BATCH_SIZE = 10_000
MAX_RETRIES = 3
RETRY_DELAYS = [2, 4, 8]


async def _request(method: str, url: str, **kwargs) -> dict:
    """Make an API request with retry logic."""
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


async def create_custom_audience(name: str, description: str) -> dict:
    """Create a new Custom Audience and return {id, name}."""
    ad_account = settings.META_AD_ACCOUNT_ID
    if not ad_account.startswith("act_"):
        ad_account = f"act_{ad_account}"

    result = await _request(
        "post",
        f"{BASE_URL}/{ad_account}/customaudiences",
        params={"access_token": settings.META_ACCESS_TOKEN},
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


async def upload_users(audience_id: str, schema: list[str], data: list[list[Any]]) -> dict:
    """Upload users to audience in batches of 10k using session-based replace.

    Using a session with last_batch_flag=True on the final batch tells Meta to
    replace the entire audience contents rather than append to existing members.
    This eliminates the need for a separate delete step.
    """
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
            params={"access_token": settings.META_ACCESS_TOKEN},
            json={
                "payload": {
                    "schema": schema,
                    "data": batch,
                },
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
    return {
        "num_received": total_received,
        "num_invalid": total_invalid,
    }


async def create_lookalike_audience(
    origin_audience_id: str, name: str
) -> dict:
    """Create a 1% Lookalike Audience (US) from the given source audience."""
    ad_account = settings.META_AD_ACCOUNT_ID
    if not ad_account.startswith("act_"):
        ad_account = f"act_{ad_account}"

    result = await _request(
        "post",
        f"{BASE_URL}/{ad_account}/customaudiences",
        params={"access_token": settings.META_ACCESS_TOKEN},
        json={
            "name": name,
            "subtype": "LOOKALIKE",
            "origin_audience_id": origin_audience_id,
            "lookalike_spec": {
                "type": "custom_ratio",
                "ratio": 0.01,
                "country": "US",
            },
        },
    )
    lookalike_id = result.get("id")
    logger.info(f"Created Lookalike Audience: {name} (ID: {lookalike_id})")
    return {"id": lookalike_id, "name": name}
