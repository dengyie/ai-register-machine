"""Environment settings for the control API."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    project_root: Path
    host: str
    port: int
    token: str | None
    max_upload_bytes: int = 20 * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    root = os.environ.get("REGISTER_PROJECT_ROOT")
    project_root = Path(root).resolve() if root else Path.cwd().resolve()
    token = os.environ.get("CONTROL_API_TOKEN")
    if token is not None and token.strip() == "":
        token = None
    host = os.environ.get("CONTROL_API_HOST", "127.0.0.1")
    port = int(os.environ.get("CONTROL_API_PORT", "8787"))
    max_upload = int(os.environ.get("CONTROL_API_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
    return Settings(
        project_root=project_root,
        host=host,
        port=port,
        token=token,
        max_upload_bytes=max_upload,
    )


def clear_settings_cache() -> None:
    get_settings.cache_clear()
