import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # AWS (for Secrets Manager)
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_SECRET_PREFIX: str = "/ghl-sync/accounts"

    # GHL — default account fallback (will be removed after YogiSoul migration)
    GHL_API_KEY: str = ""
    GHL_LOCATION_ID: str = ""
    GHL_LOCATION_NAME: str = ""

    # Meta — default account fallback
    META_ACCESS_TOKEN: str = ""
    META_AD_ACCOUNT_ID: str = ""
    META_BUSINESS_ID: str = ""

    # Claude
    CLAUDE_API_KEY: str

    # Postgres
    POSTGRES_HOST: str
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "ghl_meta_sync"
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str

    # SMTP
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_TO_EMAIL: str = ""

    # Audit AI models
    OPENAI_API_KEY: str = ""

    # App
    SYNC_SCHEDULE_CRON: str = "0 2 * * *"
    WEB_PORT: int = 9876
    LOG_LEVEL: str = "INFO"

    # Audit
    AUDIT_SCHEDULE_CRON: str = ""
    AUDIT_EMAIL_TO: str = ""

    # Stripe — default account fallback
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # Meta Conversions API — default account fallback
    META_CAPI_DATASET_ID: str = ""
    META_CAPI_ACCESS_TOKEN: str = ""
    CAPI_EVENT_NAME: str = "Purchase"
    CAPI_EVENT_SOURCE_URL: str = ""

    # Contact matching
    FUZZY_MATCH_THRESHOLD: int = 82

    # Set to a Meta test event code (e.g. TEST57877) to tag all CAPI events
    # for the Test Events tab. Remove/leave blank in production.
    CAPI_TEST_EVENT_CODE: str = ""

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()
