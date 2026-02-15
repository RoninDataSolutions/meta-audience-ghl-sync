import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GHL
    GHL_API_KEY: str
    GHL_LOCATION_ID: str
    GHL_LOCATION_NAME: str = ""

    # Meta
    META_ACCESS_TOKEN: str
    META_AD_ACCOUNT_ID: str
    META_BUSINESS_ID: str

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

    # App
    SYNC_SCHEDULE_CRON: str = "0 2 * * *"
    WEB_PORT: int = 9876
    LOG_LEVEL: str = "INFO"

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()
