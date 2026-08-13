from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings populated from environment variables when provided."""

    model_config = SettingsConfigDict(env_prefix="ETSY_EMPLOYEE_")

    data_dir: Path = Field(default_factory=lambda: Path.home() / ".etsy-performance-employee")
    database_url: str | None = None
    hermes_executable: str = "hermes"
    hermes_profile: str = "etsy-performance-us"
    hermes_timeout_seconds: float = 180
    hermes_max_turns: int = 12
    max_attachment_bytes: int = 5 * 1024 * 1024
    max_excel_upload_bytes: int = 50 * 1024 * 1024
    max_worker_event_bytes: int = 64 * 1024
    excel_cancel_timeout_seconds: float = 5.0
    excel_worker_timeout_seconds: float = 900.0
    originality_threshold: float = Field(default=0.72, ge=0.1, le=1)

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'app.db').as_posix()}"

    def ensure_runtime_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "attachments").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "excel-jobs").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "trust").mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
