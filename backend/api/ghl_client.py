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


_PRESALE_CHANNELS = {"TYPE_INSTAGRAM", "TYPE_WHATSAPP", "TYPE_FACEBOOK"}


async def fetch_conversations(days: int = 60, max_count: int = 120) -> list[dict]:
    """Fetch recent Instagram/WhatsApp/Facebook conversations from the last N days."""
    import time as _time
    cutoff_ms = (_time.time() - days * 86400) * 1000
    results: list[dict] = []
    start_after_date: int | None = None

    async with httpx.AsyncClient(timeout=30) as client:
        while len(results) < max_count:
            params: dict[str, Any] = {
                "locationId": settings.GHL_LOCATION_ID,
                "limit": 50,
            }
            if start_after_date:
                params["startAfterDate"] = start_after_date

            resp = await _request_with_retry(
                client, "GET",
                f"{BASE_URL}/conversations/search",
                headers=_headers(),
                params=params,
            )
            if not resp.is_success:
                logger.warning(f"Conversations fetch failed: {resp.status_code}")
                break

            data = resp.json()
            convs = data.get("conversations", [])
            if not convs:
                break

            for c in convs:
                last_msg_ts = c.get("lastMessageDate", 0)
                # Conversations are sorted newest-first; stop when we pass the cutoff
                if last_msg_ts < cutoff_ms:
                    return results
                if c.get("lastMessageType") in _PRESALE_CHANNELS:
                    results.append(c)
                    if len(results) >= max_count:
                        return results

            if len(convs) < 50:
                break
            start_after_date = convs[-1].get("lastMessageDate")

    logger.info(f"Fetched {len(results)} pre-sale conversations (last {days}d)")
    return results


async def fetch_conversation_messages(conv_id: str, limit: int = 20) -> list[dict]:
    """Fetch messages for a single conversation."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await _request_with_retry(
            client, "GET",
            f"{BASE_URL}/conversations/{conv_id}/messages",
            headers=_headers(),
            params={"limit": limit},
        )
        if not resp.is_success:
            return []
        data = resp.json()
        msgs = data.get("messages", {})
        # API returns {"messages": {"messages": [...], "nextPage": bool, ...}}
        if isinstance(msgs, dict):
            return msgs.get("messages", [])
        return msgs if isinstance(msgs, list) else []
