"""Account pool (cpa_auths product files) routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from apps.control_api.accounts_ops import delete_account, list_accounts
from apps.control_api.settings import get_settings

router = APIRouter(tags=["accounts"])


@router.get("/api/accounts")
def get_accounts(
    q: str = Query(default=""),
    complete: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=0, le=500),
) -> dict:
    root = get_settings().project_root
    return list_accounts(root, q=q, complete=complete, page=page, page_size=page_size)


@router.delete("/api/accounts/{name}")
def remove_account(name: str) -> dict:
    root = get_settings().project_root
    try:
        return delete_account(root, name)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
