"""Mail pool stats / probe / quarantine routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from apps.control_api import mail_ops
from apps.control_api.settings import get_settings

router = APIRouter(tags=["mail"])


class MailProbeIn(BaseModel):
    domains: list[str] = Field(default_factory=list)
    # Per-wave cap (full pool = multi-wave via offset). 500 keeps one request
    # under control_api request time without a job queue.
    limit: int = Field(default=30, ge=1, le=500)
    seed: int | None = None
    # Sequential full-pool paging. When set, order is stable (no shuffle) and
    # response carries next_offset/done for the UI/CLI to loop.
    offset: int | None = Field(default=None, ge=0)
    concurrency: int = Field(default=4, ge=1, le=8)
    wall_seconds: float = Field(default=90.0, ge=5.0, le=600.0)


class MailQuarantineIn(BaseModel):
    emails: list[str] = Field(min_length=1)
    reason: str = Field(default="quarantine", max_length=128)


class MailCompactIn(BaseModel):
    dry_run: bool = False
    drop_invalid: bool = True
    drop_comments: bool = False


def _http_from_exc(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/api/mail/pool")
def api_mail_pool() -> dict[str, Any]:
    root = get_settings().project_root
    try:
        return mail_ops.get_pool_stats(root)
    except FileNotFoundError as exc:
        raise _http_from_exc(exc) from exc


@router.post("/api/mail/probe")
def api_mail_probe(body: MailProbeIn) -> dict[str, Any]:
    root = get_settings().project_root
    domains = [d for d in (body.domains or []) if str(d).strip()] or None
    try:
        return mail_ops.probe_mail(
            root,
            domains=domains,
            limit=body.limit,
            seed=body.seed,
            offset=body.offset,
            concurrency=body.concurrency,
            wall_seconds=body.wall_seconds,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise _http_from_exc(exc) from exc


@router.post("/api/mail/quarantine")
def api_mail_quarantine(body: MailQuarantineIn) -> dict[str, Any]:
    root = get_settings().project_root
    emails = [e.strip() for e in body.emails if e and str(e).strip()]
    if not emails:
        raise HTTPException(status_code=400, detail="emails must be a non-empty list")
    try:
        return mail_ops.quarantine_mail(
            root,
            emails,
            reason=(body.reason or "quarantine").strip() or "quarantine",
        )
    except (FileNotFoundError, ValueError) as exc:
        raise _http_from_exc(exc) from exc


@router.post("/api/mail/compact")
def api_mail_compact(body: MailCompactIn | None = None) -> dict[str, Any]:
    """Dedupe live pool by email (first wins); optional drop invalid/comments."""
    root = get_settings().project_root
    body = body or MailCompactIn()
    try:
        return mail_ops.compact_mail(
            root,
            dry_run=bool(body.dry_run),
            drop_invalid=bool(body.drop_invalid),
            drop_comments=bool(body.drop_comments),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise _http_from_exc(exc) from exc


@router.get("/api/mail/cloudflare/domains")
def api_mail_cloudflare_domains() -> dict[str, Any]:
    """List domains from the configured Cloudflare temp-mail Worker."""
    root = get_settings().project_root
    try:
        return mail_ops.list_cloudflare_domains(root)
    except (ValueError, RuntimeError) as exc:
        raise _http_from_exc(exc) from exc
