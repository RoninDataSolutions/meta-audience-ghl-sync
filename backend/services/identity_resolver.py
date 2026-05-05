"""
Identity resolution: phone normalization, GHL contact matching (email → phone → fuzzy name),
and identity map cache to skip re-matching on repeat purchases.
"""
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from thefuzz import fuzz

from config import settings
from models import ContactIdentityMap

logger = logging.getLogger(__name__)


def normalize_phone(phone: str) -> str:
    """Strip formatting, remove leading US country code, return 10-digit string."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 10 else ""


def _fuzzy_name_score(a: str, b: str) -> int:
    if not a or not b:
        return 0
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return 100
    scores = [
        fuzz.ratio(a, b),
        fuzz.token_sort_ratio(a, b),
        fuzz.token_set_ratio(a, b),
        fuzz.partial_ratio(a, b),
    ]
    a_parts, b_parts = a.split(), b.split()
    if a_parts and b_parts and a_parts[0] == b_parts[0]:
        scores.append(85)
    return max(scores)


def match_by_email(email: str, contacts: list[dict]) -> dict | None:
    target = email.lower().strip()
    if not target:
        return None
    for c in contacts:
        if (c.get("email") or "").lower().strip() == target:
            return c
        for alt in c.get("additionalEmails") or []:
            if (alt or "").lower().strip() == target:
                return c
    return None


def match_by_phone(phone: str, contacts: list[dict]) -> dict | None:
    target = normalize_phone(phone)
    if not target:
        return None
    for c in contacts:
        if normalize_phone(c.get("phone") or "") == target:
            return c
        for alt in c.get("additionalPhones") or []:
            if normalize_phone(alt or "") == target:
                return c
    return None


def match_by_name_fuzzy(
    name: str,
    contacts: list[dict],
    threshold: int = 82,
) -> tuple[dict | None, int, list[dict]]:
    """Returns (best_contact, score, top_3_candidates)."""
    if not name:
        return None, 0, []

    candidates = []
    for c in contacts:
        first = (c.get("firstName") or "").strip()
        last = (c.get("lastName") or "").strip()
        full = f"{first} {last}".strip()
        name_field = (c.get("contactName") or c.get("name") or "").strip()

        score = max(
            _fuzzy_name_score(name, full) if full else 0,
            _fuzzy_name_score(name, name_field) if name_field else 0,
        )
        if score >= 50:
            candidates.append({"contact": c, "score": score, "ghl_name": full or name_field})

    candidates.sort(key=lambda x: x["score"], reverse=True)
    top_3 = [{"id": c["contact"]["id"], "name": c["ghl_name"], "score": c["score"]}
             for c in candidates[:3]]

    if candidates and candidates[0]["score"] >= threshold:
        return candidates[0]["contact"], candidates[0]["score"], top_3
    return None, 0, top_3


# ── Identity map (DB cache) ──────────────────────────────────────────────────

def check_identity_map(
    db: Session,
    stripe_customer_id: str | None,
    stripe_email: str,
) -> str | None:
    """Return cached ghl_contact_id if we've matched this customer before."""
    if stripe_customer_id:
        row = db.query(ContactIdentityMap).filter_by(
            stripe_customer_id=stripe_customer_id
        ).first()
        if row:
            return row.ghl_contact_id

    if stripe_email:
        row = db.query(ContactIdentityMap).filter_by(
            stripe_email=stripe_email.lower().strip()
        ).first()
        if row:
            return row.ghl_contact_id

    return None


def save_identity_map(
    db: Session,
    stripe_customer_id: str | None,
    stripe_email: str,
    ghl_contact_id: str,
    match_method: str,
    match_score: int | None = None,
) -> None:
    email = (stripe_email or "").lower().strip() or None

    # Upsert by stripe_customer_id
    if stripe_customer_id:
        existing = db.query(ContactIdentityMap).filter_by(
            stripe_customer_id=stripe_customer_id,
            ghl_contact_id=ghl_contact_id,
        ).first()
        if not existing:
            try:
                db.add(ContactIdentityMap(
                    stripe_customer_id=stripe_customer_id,
                    stripe_email=email,
                    ghl_contact_id=ghl_contact_id,
                    match_method=match_method,
                    match_score=match_score,
                ))
                db.commit()
            except Exception:
                db.rollback()

    # Upsert by email (separate row if no customer_id)
    if email and not stripe_customer_id:
        existing = db.query(ContactIdentityMap).filter_by(
            stripe_email=email,
            ghl_contact_id=ghl_contact_id,
        ).first()
        if not existing:
            try:
                db.add(ContactIdentityMap(
                    stripe_customer_id=None,
                    stripe_email=email,
                    ghl_contact_id=ghl_contact_id,
                    match_method=match_method,
                    match_score=match_score,
                ))
                db.commit()
            except Exception:
                db.rollback()


# ── Full match cascade ───────────────────────────────────────────────────────

async def match_stripe_to_ghl(
    stripe_data: dict,
    contacts: list[dict],
    db: Session,
) -> dict:
    """
    Run: identity_map → email_exact → phone_exact → name_fuzzy.
    Returns dict with ghl_contact, match_method, match_score, match_candidates.
    """
    result: dict = {
        "ghl_contact": None,
        "match_method": "none",
        "match_score": None,
        "match_candidates": [],
    }

    cid = stripe_data.get("customer_id")
    email = stripe_data.get("email", "")
    phone = stripe_data.get("phone", "")
    name = stripe_data.get("name", "")

    # Step 0: identity map cache
    cached_ghl_id = check_identity_map(db, cid, email)
    if cached_ghl_id:
        contact = next((c for c in contacts if c.get("id") == cached_ghl_id), None)
        if contact:
            result["ghl_contact"] = contact
            result["match_method"] = "identity_map"
            return result

    # Step 1: exact email
    if email:
        contact = match_by_email(email, contacts)
        if contact:
            result.update(ghl_contact=contact, match_method="email_exact")
            save_identity_map(db, cid, email, contact["id"], "email_exact")
            return result

    # Step 2: exact phone
    if phone:
        contact = match_by_phone(phone, contacts)
        if contact:
            result.update(ghl_contact=contact, match_method="phone_exact")
            save_identity_map(db, cid, email, contact["id"], "phone_exact")
            return result

    # Step 3: fuzzy name
    if name:
        threshold = settings.FUZZY_MATCH_THRESHOLD
        contact, score, candidates = match_by_name_fuzzy(name, contacts, threshold)
        result["match_candidates"] = candidates
        if contact:
            result.update(ghl_contact=contact, match_method="name_fuzzy", match_score=score)
            save_identity_map(db, cid, email, contact["id"], "name_fuzzy", score)
            return result

    return result
