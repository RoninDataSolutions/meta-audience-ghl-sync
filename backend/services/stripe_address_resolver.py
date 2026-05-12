"""
Resolve US state from Stripe PaymentIntent billing addresses.

We leverage the EXISTING fuzzy match already done during transaction sync:
the `stripe_transactions` table has `ghl_contact_id` populated for every
Stripe payment that matched a GHL contact (via email → phone → fuzzy name).

For each paying GHL contact we want to enrich:
  1. Find their most recent matching `stripe_transactions` row (already linked).
  2. Use the stored `stripe_payment_id` to fetch the PaymentIntent from Stripe.
  3. Extract `latest_charge.billing_details.address.state` — the address the
     customer typed at checkout. This is the strongest geographic signal.

No re-matching by email — that path fails for guest checkouts where Stripe
doesn't create a Customer record.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from config import settings
from models import StripeTransaction

if TYPE_CHECKING:
    from services.credential_resolver import AccountCredentials

logger = logging.getLogger(__name__)


def _extract_address(addr_obj) -> dict | None:
    """Pull state/postal/city/country from a Stripe address StripeObject."""
    if not addr_obj:
        return None
    state = getattr(addr_obj, "state", None) or ""
    if not state:
        return None
    return {
        "state": state,
        "postal_code": getattr(addr_obj, "postal_code", None) or "",
        "city": getattr(addr_obj, "city", None) or "",
        "country": getattr(addr_obj, "country", None) or "",
    }


async def resolve_addresses_for_contacts(
    contact_ids: list[str],
    db: Session,
    creds: "AccountCredentials | None" = None,
    concurrency: int = 10,
) -> dict[str, dict[str, str]]:
    """
    For each ghl_contact_id, find the most recent matched Stripe transaction
    (via the already-populated `stripe_transactions.ghl_contact_id` linkage),
    then fetch its PaymentIntent and extract billing address.

    Returns {ghl_contact_id: {"state": "TX", "postal_code": "77001", ...}, ...}
    """
    api_key = (creds.stripe_secret_key if creds else None) or settings.STRIPE_SECRET_KEY
    if not api_key or not contact_ids:
        return {}

    # Look up the most recent stripe_transaction per contact via fuzzy match results
    txns = (
        db.query(StripeTransaction)
        .filter(
            StripeTransaction.ghl_contact_id.in_(contact_ids),
            StripeTransaction.status == "succeeded",
        )
        .order_by(StripeTransaction.stripe_created_at.desc())
        .all()
    )

    # Pick the most recent per contact
    most_recent: dict[str, StripeTransaction] = {}
    for t in txns:
        if t.ghl_contact_id and t.ghl_contact_id not in most_recent:
            most_recent[t.ghl_contact_id] = t

    if not most_recent:
        logger.info("No matched stripe_transactions found for paying contacts (run transaction sync first)")
        return {}

    logger.info(f"Found {len(most_recent)}/{len(contact_ids)} contacts with matched Stripe transactions")

    import stripe as stripe_lib
    stripe_lib.api_key = api_key

    semaphore = asyncio.Semaphore(concurrency)
    out: dict[str, dict[str, str]] = {}

    async def fetch_one(contact_id: str, txn: StripeTransaction):
        pi_id = txn.stripe_payment_id
        if not pi_id or not pi_id.startswith("pi_"):
            return
        async with semaphore:
            try:
                pi = await asyncio.to_thread(
                    stripe_lib.PaymentIntent.retrieve,
                    pi_id,
                    expand=["latest_charge"],
                )
            except Exception as ex:
                logger.debug(f"PaymentIntent.retrieve failed for {pi_id}: {ex}")
                return

        # Try latest_charge.billing_details.address first
        latest_charge = getattr(pi, "latest_charge", None)
        if latest_charge and not isinstance(latest_charge, str):
            bd = getattr(latest_charge, "billing_details", None)
            addr = _extract_address(getattr(bd, "address", None) if bd else None)
            if addr:
                out[contact_id] = addr
                return

        # Fallback: shipping address on PaymentIntent
        shipping = getattr(pi, "shipping", None)
        if shipping:
            addr = _extract_address(getattr(shipping, "address", None))
            if addr:
                out[contact_id] = addr
                return

    await asyncio.gather(*[
        fetch_one(cid, txn) for cid, txn in most_recent.items()
    ])
    return out
