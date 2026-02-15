import asyncio
import logging
import time
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://services.leadconnectorhq.com"

# Simple TTL cache
_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 300  # 5 minutes


def _get_cached(key: str) -> Any | None:
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
        del _cache[key]
    return None


def _set_cached(key: str, data: Any):
    _cache[key] = (time.time(), data)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.GHL_API_KEY}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }


async def _request_with_retry(
    client: httpx.AsyncClient, method: str, url: str, max_retries: int = 4, **kwargs
) -> httpx.Response:
    """Make a request with exponential backoff on 429."""
    for attempt in range(max_retries + 1):
        resp = await client.request(method, url, **kwargs)
        if resp.status_code != 429:
            return resp
        if attempt == max_retries:
            return resp
        wait = 2 ** attempt
        logger.warning(f"GHL rate limited (429), retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
        await asyncio.sleep(wait)
    return resp


async def get_custom_fields() -> list[dict]:
    cached = _get_cached("custom_fields")
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await _request_with_retry(
            client, "GET",
            f"{BASE_URL}/locations/{settings.GHL_LOCATION_ID}/customFields",
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        fields = data.get("customFields", [])
        _set_cached("custom_fields", fields)
        logger.info(f"Fetched {len(fields)} custom fields from GHL")
        return fields


async def get_all_contacts() -> list[dict]:
    """Fetch all contacts from the location using cursor-based pagination."""
    all_contacts: list[dict] = []
    seen_ids: set[str] = set()
    limit = 100
    start_after: str | None = None
    start_after_id: str | None = None

    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            params: dict[str, Any] = {
                "locationId": settings.GHL_LOCATION_ID,
                "limit": limit,
            }
            if start_after_id:
                params["startAfterId"] = start_after_id
            if start_after:
                params["startAfter"] = start_after

            resp = await _request_with_retry(
                client, "GET",
                f"{BASE_URL}/contacts/",
                headers=_headers(),
                params=params,
            )
            if resp.status_code != 200:
                logger.error(f"Fetch contacts failed: {resp.status_code} {resp.text}")
                break

            data = resp.json()
            contacts = data.get("contacts", [])
            if not contacts:
                break

            # Deduplicate — safety net against pagination loops
            new_contacts = [c for c in contacts if c.get("id") and c["id"] not in seen_ids]
            if not new_contacts:
                logger.warning("Pagination loop detected, stopping")
                break
            for c in new_contacts:
                seen_ids.add(c["id"])
            all_contacts.extend(new_contacts)

            logger.info(f"Fetched {len(all_contacts)} contacts so far...")

            # GHL returns fewer than limit when we've reached the end
            if len(contacts) < limit:
                break

            # Read pagination cursors from response meta
            meta = data.get("meta", {})
            start_after_id = meta.get("startAfterId") or meta.get("nextPageUrl")
            start_after = meta.get("startAfter")
            if not start_after_id and not start_after:
                # Fallback: use last contact ID
                start_after_id = contacts[-1].get("id")
                if not start_after_id:
                    break

            await asyncio.sleep(0.5)  # Rate limit courtesy

    logger.info(f"Fetched {len(all_contacts)} total contacts from location")
    return all_contacts
