"""Config GET/PUT routes."""

from __future__ import annotations

from fastapi import APIRouter

from apps.control_api.config_io import load_config, redact_config, save_config
from apps.control_api.schemas import ConfigOut, ConfigPutIn, ConfigPutOut
from apps.control_api.settings import get_settings

router = APIRouter(tags=["config"])


@router.get("/api/config", response_model=ConfigOut)
def get_config() -> ConfigOut:
    root = get_settings().project_root
    data = load_config(root)
    return ConfigOut(config=redact_config(data), path=str(root / "config.json"))


@router.put("/api/config", response_model=ConfigPutOut)
def put_config(body: ConfigPutIn) -> ConfigPutOut:
    root = get_settings().project_root
    result = save_config(root, body.config)
    return ConfigPutOut(
        ok=True,
        backup=result.get("backup"),
        changed_keys=list(result.get("changed_keys") or []),
        config=result["config"],
    )
