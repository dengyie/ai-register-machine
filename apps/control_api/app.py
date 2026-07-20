"""FastAPI application factory for the project control plane."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.control_api.auth import require_token
from apps.control_api.schemas import HealthOut, OverviewOut
from apps.control_api.settings import get_settings


def create_app() -> FastAPI:
    app = FastAPI(title="ai-register-machine control API", version="1.0.0")

    @app.get("/api/health", response_model=HealthOut)
    def health() -> HealthOut:
        s = get_settings()
        return HealthOut(
            ok=True,
            project_root=str(s.project_root),
            token_required=bool(s.token),
        )

    @app.get("/api/overview", response_model=OverviewOut, dependencies=[Depends(require_token)])
    def overview() -> OverviewOut:
        # Full overview wired in later tasks; stub keeps auth tests green.
        from apps.control_api.overview import build_overview

        s = get_settings()
        return OverviewOut(**build_overview(s.project_root))

    # Lazy-register remaining routers when modules exist (filled by later tasks).
    try:
        from apps.control_api.routes_config import router as config_router

        app.include_router(config_router, dependencies=[Depends(require_token)])
    except ImportError:
        pass
    try:
        from apps.control_api.routes_runs import router as runs_router

        app.include_router(runs_router, dependencies=[Depends(require_token)])
    except ImportError:
        pass
    try:
        from apps.control_api.routes_import import router as import_router

        app.include_router(import_router, dependencies=[Depends(require_token)])
    except ImportError:
        pass

    web_dir = Path(__file__).resolve().parents[1] / "web"
    if web_dir.is_dir() and (web_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    @app.exception_handler(ValueError)
    async def _value_error(_request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app
