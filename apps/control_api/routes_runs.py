"""Run control routes."""

from __future__ import annotations

from fastapi import APIRouter, Query

from apps.control_api.runs import list_runs, run_status, start_run, stop_run, tail_log
from apps.control_api.schemas import LogsOut, RunActionOut, StartRunRequest
from apps.control_api.settings import get_settings

router = APIRouter(tags=["runs"])


@router.get("/api/runs")
def get_runs() -> dict:
    root = get_settings().project_root
    return {"runs": list_runs(root), "current": run_status(root)}


@router.get("/api/runs/current")
def get_current() -> dict:
    root = get_settings().project_root
    return {"run": run_status(root)}


@router.get("/api/runs/current/logs", response_model=LogsOut)
def get_current_logs(
    tail: int = Query(default=200, ge=1, le=5000),
    which: str = Query(default="auto", pattern="^(auto|supervisor|worker|both)$"),
) -> LogsOut:
    root = get_settings().project_root
    data = tail_log(root, n=tail, which=which)
    return LogsOut(path=data.get("path"), text=data.get("text") or "")


@router.post("/api/runs/start", response_model=RunActionOut)
def post_start(body: StartRunRequest) -> RunActionOut:
    root = get_settings().project_root
    result = start_run(root, body)
    return RunActionOut(ok=True, run=result.get("run"), detail=result.get("detail", ""))


@router.post("/api/runs/stop", response_model=RunActionOut)
def post_stop() -> RunActionOut:
    root = get_settings().project_root
    result = stop_run(root)
    # Surface pid/source/mode in detail so UI never looks like a silent no-op.
    detail = str(result.get("detail") or "")
    extra = []
    if result.get("pid") is not None:
        extra.append(f"pid={result.get('pid')}")
    if result.get("source"):
        extra.append(f"source={result.get('source')}")
    if result.get("mode"):
        extra.append(f"mode={result.get('mode')}")
    if extra:
        detail = f"{detail} ({', '.join(extra)})" if detail else ", ".join(extra)
    return RunActionOut(ok=bool(result.get("ok")), run=result.get("run"), detail=detail)
