from pathlib import Path

from pydantic import Field

from pachifast.config import PachifastSettingsBase, get_settings


class Settings(PachifastSettingsBase):
    demo_upload_dir: str = Field(default="uploads", validation_alias="DEMO_UPLOAD_DIR")
    demo_max_upload_bytes: int = Field(default=5 * 1024 * 1024, validation_alias="DEMO_MAX_UPLOAD_BYTES")


settings = Settings()

ALLOWED_UPLOAD_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}


def get_database_url() -> str:
    """The demo tables live in the auth database: no extra database to configure."""
    return get_settings().AUTH_DATABASE_URL


def upload_dir() -> Path:
    """Directory of the uploaded files (created when missing)."""
    path = Path(settings.demo_upload_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path
