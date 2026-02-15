import hashlib
import re


def _sha256(value: str) -> str:
    """SHA256 hash a lowercase, trimmed string."""
    return hashlib.sha256(value.lower().strip().encode("utf-8")).hexdigest()


def hash_email(email: str | None) -> str:
    if not email or not email.strip():
        return ""
    return _sha256(email)


def hash_phone(phone: str | None) -> str:
    """Normalize phone to E.164 format then hash."""
    if not phone or not phone.strip():
        return ""
    digits = re.sub(r"[^\d]", "", phone.strip())
    if len(digits) == 10:
        digits = "1" + digits
    if not digits.startswith("+"):
        digits = "+" + digits
    return _sha256(digits)


def hash_name(name: str | None) -> str:
    if not name or not name.strip():
        return ""
    return _sha256(name)


def hash_field(value: str | None) -> str:
    if not value or not value.strip():
        return ""
    return _sha256(value)


def prepare_contact_row(
    contact: dict, normalized_value: int
) -> list:
    """Prepare a single contact row for Meta upload.
    Schema: [EMAIL, PHONE, FN, LN, CT, ST, ZIP, COUNTRY, VALUE]
    """
    return [
        hash_email(contact.get("email")),
        hash_phone(contact.get("phone")),
        hash_name(contact.get("firstName")),
        hash_name(contact.get("lastName")),
        hash_field(contact.get("city")),
        hash_field(contact.get("state")),
        hash_field(contact.get("postalCode")),
        (contact.get("country") or "us").lower().strip(),
        normalized_value,
    ]
