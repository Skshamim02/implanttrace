from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-change-me-dev-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"  # development | test | production
    database_url: str = "sqlite:///./implanttrace.db"

    # Security
    secret_key: str = DEV_SECRET
    session_hours: int = 9  # one shift plus handover
    cookie_secure: bool = False  # True behind HTTPS in production
    password_min_length: int = 10
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # Inspection import
    inbox_dir: str = "inbox"
    max_upload_mb: int = 5

    # Notifications (all optional; if unset, alerts are only logged)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "implanttrace@localhost"
    quality_alert_emails: str = ""  # comma-separated
    teams_webhook_url: str = ""

    # Logging
    log_level: str = "INFO"
    log_format: str = "text"  # text | json

    @cached_property
    def alert_recipients(self) -> list[str]:
        return [e.strip() for e in self.quality_alert_emails.split(",") if e.strip()]

    def check(self) -> None:
        """Refuse to start in production with unsafe settings."""
        if self.environment == "production":
            if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
                raise RuntimeError("SECRET_KEY must be set to a random value of 32+ characters")
            if not self.cookie_secure:
                raise RuntimeError("COOKIE_SECURE must be true in production (serve over HTTPS)")


settings = Settings()
