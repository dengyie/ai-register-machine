"""Config GET/PUT routes."""

from __future__ import annotations

from fastapi import APIRouter

from apps.control_api.config_io import (
    enrich_config_from_env,
    load_config,
    redact_config,
    save_config,
)
from apps.control_api.schemas import ConfigOut, ConfigPutIn, ConfigPutOut
from apps.control_api.settings import get_settings

router = APIRouter(tags=["config"])


@router.get("/api/config", response_model=ConfigOut)
def get_config() -> ConfigOut:
    """Return config with blank operational fields filled from ``.env``.

    Disk ``config.json`` stays as-is; response enrichment makes the console
    show proxy/domains the batch actually uses when only ``.env`` has them.
    """
    root = get_settings().project_root
    data = enrich_config_from_env(root, load_config(root))
    return ConfigOut(config=redact_config(data), path=str(root / "config.json"))


@router.put("/api/config", response_model=ConfigPutOut)
def put_config(body: ConfigPutIn) -> ConfigPutOut:
    """Persist config.json and mirror operational keys into ``.env``.

    Batch supervisors load ``.env``; without this sync the console UI could
    show hotmail while live register_cli still used a stale EMAIL_PROVIDER.
    Sticky-empty: blank proxy/defaultDomains in the payload do not wipe prior
    non-empty values in config or ``.env``.
    """
    root = get_settings().project_root
    result = save_config(root, body.config)
    # Re-read disk after save, then enrich once and redact once (same as GET).
    # Do not enrich from save_config's already-redacted payload.
    enriched = enrich_config_from_env(root, load_config(root))
    return ConfigPutOut(
        ok=True,
        backup=result.get("backup"),
        changed_keys=list(result.get("changed_keys") or []),
        changed_env_keys=list(result.get("changed_env_keys") or []),
        env_backup=result.get("env_backup"),
        config=redact_config(enriched),
    )
