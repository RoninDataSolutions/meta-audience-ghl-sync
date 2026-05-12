import asyncio
import logging
import time
from typing import Any, TYPE_CHECKING

import httpx

from config import settings

if TYPE_CHECKING:
    from services.credential_resolver import AccountCredentials

logger = logging.getLogger(__name__)

BASE_URL = "https://services.leadconnectorhq.com"

# TTL cache keyed by (location_id, cache_key)
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


def _api_key(creds: "AccountCredentials | None") -> str:
    return creds.ghl_api_key if creds else settings.GHL_API_KEY


def _location_id(creds: "AccountCredentials | None") -> str:
    return creds.ghl_location_id if creds else settings.GHL_LOCATION_ID


def _headers(creds: "AccountCredentials | None" = None) -> dict:
    return {
        "Authorization": f"Bearer {_api_key(creds)}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }


async def _request_with_retry(
    client: httpx.AsyncClient, method: str, url: str, max_retries: int = 4, **kwargs
) -> httpx.Response:
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


async def get_custom_fields(creds: "AccountCredentials | None" = None) -> list[dict]:
    loc = _location_id(creds)
    cache_key = f"custom_fields:{loc}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await _request_with_retry(
            client, "GET",
            f"{BASE_URL}/locations/{loc}/customFields",
            headers=_headers(creds),
        )
        resp.raise_for_status()
        data = resp.json()
        fields = data.get("customFields", [])
        _set_cached(cache_key, fields)
        logger.info(f"Fetched {len(fields)} custom fields from GHL ({loc})")
        return fields


async def get_contact_detail(
    contact_id: str,
    creds: "AccountCredentials | None" = None,
) -> dict | None:
    """
    Fetch a single contact's full record, including address1, city, state, postalCode.
    Returns None on 404. Raises on other failures.
    The list endpoint omits address fields — only this endpoint returns them.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await _request_with_retry(
            client, "GET",
            f"{BASE_URL}/contacts/{contact_id}",
            headers=_headers(creds),
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        # GHL returns {"contact": {...}}; unwrap if needed
        return data.get("contact") if isinstance(data, dict) and "contact" in data else data


async def enrich_contacts_with_address(
    contacts: list[dict],
    creds: "AccountCredentials | None" = None,
    concurrency: int = 10,
) -> list[dict]:
    """
    For each contact in the input list, fetch its detail record and merge
    address fields (state, city, postalCode, address1) back onto the contact.
    Returns the enriched list (same order, same length).

    Use this *after* filtering to a small set (e.g. paying customers) — running
    against 700+ contacts will take ~70s.
    """
    if not contacts:
        return contacts

    semaphore = asyncio.Semaphore(concurrency)
    ADDR_FIELDS = ("address1", "city", "state", "postalCode", "country")

    async def fetch_one(c: dict) -> dict:
        cid = c.get("id")
        if not cid:
            return c
        async with semaphore:
            try:
                detail = await get_contact_detail(cid, creds=creds)
            except Exception as e:
                logger.warning(f"Detail fetch failed for {cid}: {e}")
                return c
        if not detail:
            return c
        # Merge only fields the list endpoint didn't already populate
        for f in ADDR_FIELDS:
            if not c.get(f) and detail.get(f):
                c[f] = detail[f]
        return c

    results = await asyncio.gather(*[fetch_one(c) for c in contacts])
    return list(results)


async def get_all_contacts(creds: "AccountCredentials | None" = None) -> list[dict]:
    """Fetch all contacts from the location using cursor-based pagination."""
    loc = _location_id(creds)
    all_contacts: list[dict] = []
    seen_ids: set[str] = set()
    limit = 100
    start_after: str | None = None
    start_after_id: str | None = None

    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            params: dict[str, Any] = {"locationId": loc, "limit": limit}
            if start_after_id:
                params["startAfterId"] = start_after_id
            if start_after:
                params["startAfter"] = start_after

            resp = await _request_with_retry(
                client, "GET",
                f"{BASE_URL}/contacts/",
                headers=_headers(creds),
                params=params,
            )
            if resp.status_code != 200:
                logger.error(f"Fetch contacts failed: {resp.status_code} {resp.text}")
                break

            data = resp.json()
            contacts = data.get("contacts", [])
            if not contacts:
                break

            new_contacts = [c for c in contacts if c.get("id") and c["id"] not in seen_ids]
            if not new_contacts:
                logger.warning("Pagination loop detected, stopping")
                break
            for c in new_contacts:
                seen_ids.add(c["id"])
            all_contacts.extend(new_contacts)
            logger.info(f"Fetched {len(all_contacts)} contacts so far...")

            if len(contacts) < limit:
                break

            meta = data.get("meta", {})
            start_after_id = meta.get("startAfterId") or meta.get("nextPageUrl")
            start_after = meta.get("startAfter")
            if not start_after_id and not start_after:
                start_after_id = contacts[-1].get("id")
                if not start_after_id:
                    break

            await asyncio.sleep(0.5)

    logger.info(f"Fetched {len(all_contacts)} total contacts from location {loc}")
    return all_contacts


_PRESALE_CHANNELS = {"TYPE_INSTAGRAM", "TYPE_WHATSAPP", "TYPE_FACEBOOK"}


async def fetch_conversations(
    days: int = 60,
    max_count: int = 120,
    creds: "AccountCredentials | None" = None,
) -> list[dict]:
    import time as _time
    loc = _location_id(creds)
    cutoff_ms = (_time.time() - days * 86400) * 1000
    results: list[dict] = []
    start_after_date: int | None = None

    async with httpx.AsyncClient(timeout=30) as client:
        while len(results) < max_count:
            params: dict[str, Any] = {"locationId": loc, "limit": 50}
            if start_after_date:
                params["startAfterDate"] = start_after_date

            resp = await _request_with_retry(
                client, "GET",
                f"{BASE_URL}/conversations/search",
                headers=_headers(creds),
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
                if last_msg_ts < cutoff_ms:
                    return results
                if c.get("lastMessageType") in _PRESALE_CHANNELS:
                    results.append(c)
                    if len(results) >= max_count:
                        return results

            if len(convs) < 50:
                break
            start_after_date = convs[-1].get("lastMessageDate")

    logger.info(f"Fetched {len(results)} pre-sale conversations (last {days}d, loc={loc})")
    return results


async def fetch_conversation_messages(
    conv_id: str,
    limit: int = 20,
    creds: "AccountCredentials | None" = None,
) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await _request_with_retry(
            client, "GET",
            f"{BASE_URL}/conversations/{conv_id}/messages",
            headers=_headers(creds),
            params={"limit": limit},
        )
        if not resp.is_success:
            return []
        data = resp.json()
        msgs = data.get("messages", {})
        if isinstance(msgs, dict):
            return msgs.get("messages", [])
        return msgs if isinstance(msgs, list) else []
