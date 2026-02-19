"""Set required env vars before any backend module is imported."""
import os

os.environ.setdefault("GHL_API_KEY", "test-ghl-key")
os.environ.setdefault("GHL_LOCATION_ID", "test-location-id")
os.environ.setdefault("GHL_LOCATION_NAME", "Test Location")
os.environ.setdefault("META_ACCESS_TOKEN", "test-meta-token")
os.environ.setdefault("META_AD_ACCOUNT_ID", "123456789")
os.environ.setdefault("META_BUSINESS_ID", "987654321")
os.environ.setdefault("CLAUDE_API_KEY", "test-claude-key")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
