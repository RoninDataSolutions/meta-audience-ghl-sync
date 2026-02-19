"""Tests for LTV field resolution and extraction logic in sync_service."""
import pytest

from services.sync_service import _resolve_ltv_field_uuid, _extract_ltv

# ---------------------------------------------------------------------------
# _resolve_ltv_field_uuid
# ---------------------------------------------------------------------------

CUSTOM_FIELDS = [
    {"id": "uuid-001", "fieldKey": "contact.revenue", "name": "Revenue"},
    {"id": "uuid-ltv", "fieldKey": "contact.ltv", "name": "LTV"},
    {"id": "uuid-003", "fieldKey": "contact.score", "name": "Score"},
]


class TestResolveLtvFieldUuid:
    def test_resolves_by_field_key(self):
        uuid = _resolve_ltv_field_uuid(CUSTOM_FIELDS, "contact.ltv")
        assert uuid == "uuid-ltv"

    def test_resolves_by_uuid_directly(self):
        """Supports passing the UUID itself as the field key (fallback mode)."""
        uuid = _resolve_ltv_field_uuid(CUSTOM_FIELDS, "uuid-ltv")
        assert uuid == "uuid-ltv"

    def test_raises_when_field_not_found(self):
        """Sync must fail clearly when the configured LTV field doesn't exist."""
        with pytest.raises(ValueError, match="not found in GHL location"):
            _resolve_ltv_field_uuid(CUSTOM_FIELDS, "contact.nonexistent")

    def test_raises_with_available_fields_listed(self):
        """Error message includes available field keys to aid debugging."""
        with pytest.raises(ValueError, match="contact.revenue"):
            _resolve_ltv_field_uuid(CUSTOM_FIELDS, "contact.missing")

    def test_raises_on_empty_field_list(self):
        with pytest.raises(ValueError):
            _resolve_ltv_field_uuid([], "contact.ltv")


# ---------------------------------------------------------------------------
# _extract_ltv
# ---------------------------------------------------------------------------

class TestExtractLtv:
    def test_returns_float_when_found(self):
        contact = {"customFields": [{"id": "uuid-ltv", "value": "150.50"}]}
        assert _extract_ltv(contact, "uuid-ltv") == 150.50

    def test_returns_none_when_uuid_not_present(self):
        contact = {"customFields": [{"id": "uuid-other", "value": "99"}]}
        assert _extract_ltv(contact, "uuid-ltv") is None

    def test_returns_none_when_no_custom_fields(self):
        contact = {}
        assert _extract_ltv(contact, "uuid-ltv") is None

    def test_returns_none_when_custom_fields_empty(self):
        contact = {"customFields": []}
        assert _extract_ltv(contact, "uuid-ltv") is None

    def test_returns_zero_for_empty_string_value(self):
        """Empty string value treated as 0 (field exists but no data entered)."""
        contact = {"customFields": [{"id": "uuid-ltv", "value": ""}]}
        assert _extract_ltv(contact, "uuid-ltv") == 0.0

    def test_returns_zero_for_null_value(self):
        contact = {"customFields": [{"id": "uuid-ltv", "value": None}]}
        assert _extract_ltv(contact, "uuid-ltv") == 0.0

    def test_returns_none_for_non_numeric_value(self):
        contact = {"customFields": [{"id": "uuid-ltv", "value": "N/A"}]}
        assert _extract_ltv(contact, "uuid-ltv") is None

    def test_integer_string_value(self):
        contact = {"customFields": [{"id": "uuid-ltv", "value": "500"}]}
        assert _extract_ltv(contact, "uuid-ltv") == 500.0

    def test_matches_only_exact_uuid(self):
        """Does not partially match UUID prefixes."""
        contact = {"customFields": [{"id": "uuid-ltv-extra", "value": "100"}]}
        assert _extract_ltv(contact, "uuid-ltv") is None


# ---------------------------------------------------------------------------
# LTV extraction loop — null defaults to 0
# ---------------------------------------------------------------------------

class TestLtvExtractionLoop:
    """Verify the `or 0.0` defaulting applied in run_sync."""

    def _extract_all(self, contacts, field_uuid):
        return [_extract_ltv(c, field_uuid) or 0.0 for c in contacts]

    def test_contact_without_ltv_field_gets_zero(self):
        contacts = [{"customFields": []}]
        values = self._extract_all(contacts, "uuid-ltv")
        assert values == [0.0]

    def test_contact_with_ltv_keeps_value(self):
        contacts = [{"customFields": [{"id": "uuid-ltv", "value": "250"}]}]
        values = self._extract_all(contacts, "uuid-ltv")
        assert values == [250.0]

    def test_mixed_contacts(self):
        """Contacts with and without LTV — null ones default to 0."""
        contacts = [
            {"customFields": [{"id": "uuid-ltv", "value": "100"}]},
            {"customFields": []},                                        # no LTV
            {"customFields": [{"id": "uuid-ltv", "value": "N/A"}]},     # invalid → None → 0
            {"customFields": [{"id": "uuid-ltv", "value": "300"}]},
        ]
        values = self._extract_all(contacts, "uuid-ltv")
        assert values == [100.0, 0.0, 0.0, 300.0]

    def test_nonzero_count(self):
        contacts = [
            {"customFields": [{"id": "uuid-ltv", "value": "100"}]},
            {"customFields": []},
            {"customFields": [{"id": "uuid-ltv", "value": "200"}]},
        ]
        values = self._extract_all(contacts, "uuid-ltv")
        nonzero = sum(1 for v in values if v > 0)
        assert nonzero == 2

    def test_all_zero_when_no_ltv_data(self):
        contacts = [{"customFields": []}, {"customFields": []}, {"customFields": []}]
        values = self._extract_all(contacts, "uuid-ltv")
        assert all(v == 0.0 for v in values)
        assert sum(1 for v in values if v > 0) == 0
