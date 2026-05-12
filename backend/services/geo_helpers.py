"""
Geographic helpers for the audit.

Normalizes US state names/codes consistently between Meta's `region` breakdown
(which returns full names like "California") and GHL contacts (which may store
"CA", "California", or "california").
"""

# Full name → 2-letter code
STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "puerto rico": "PR",
}

# Reverse: 2-letter code → full name (for display)
STATE_CODE_TO_NAME = {v: k.title() for k, v in STATE_NAME_TO_CODE.items()}


def normalize_state(raw: str | None) -> str | None:
    """Return the 2-letter state code, or None if unrecognized."""
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Already a 2-letter code?
    if len(s) == 2 and s.upper() in STATE_CODE_TO_NAME:
        return s.upper()
    # Full name (any casing)
    return STATE_NAME_TO_CODE.get(s.lower())


def state_display_name(code: str | None) -> str | None:
    if not code:
        return None
    return STATE_CODE_TO_NAME.get(code.upper())
