"""Node catalog + Clash routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from apps.control_api import nodes_ops
from apps.control_api.settings import get_settings

router = APIRouter(tags=["nodes"])


class NodeAddIn(BaseModel):
    url: str = Field(min_length=3, max_length=2000)
    label: str = Field(default="", max_length=128)
    tags: list[str] = Field(default_factory=list)
    tier: int = Field(default=0, ge=0, le=1)
    enabled: bool = True


class NodeTestIn(BaseModel):
    ids: list[str] = Field(default_factory=list)
    timeout: float = Field(default=12.0, ge=1.0, le=60.0)
    limit: int = Field(default=50, ge=1, le=500)


class ClashTestIn(BaseModel):
    names: list[str] = Field(default_factory=list)
    timeout_ms: int = Field(default=5000, ge=500, le=30000)
    limit: int = Field(default=40, ge=1, le=500)


class ClashSubImportIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    group: str = Field(default="🎯Grok注册", max_length=128)
    groups: list[str] = Field(default_factory=list)
    prefix: str = Field(default="SUB", max_length=32)
    mode: str = Field(default="merge", max_length=32)  # merge | replace_prefix
    dry_run: bool = False
    reload: bool = True
    timeout: float = Field(default=25.0, ge=3.0, le=120.0)
    max_proxies: int = Field(default=400, ge=1, le=2000)


class ClashDeletePrefixIn(BaseModel):
    prefix: str = Field(min_length=1, max_length=32)
    dry_run: bool = False
    reload: bool = True
    force: bool = False  # required for protected prefixes (e.g. GVPS)


class ClashPruneUnhealthyIn(BaseModel):
    prefix: str = Field(default="", max_length=32)
    group: str = Field(default="🎯Grok注册", max_length=128)
    timeout_ms: int = Field(default=4000, ge=500, le=30000)
    # keep wall-clock under Cloudflare ~100s when console is proxied
    limit: int = Field(default=120, ge=1, le=300)
    dry_run: bool = False
    reload: bool = True
    delete_defs: bool = True  # False = only strip from groups


class EnabledIn(BaseModel):
    enabled: bool = True


@router.get("/api/nodes")
def get_nodes(
    q: str = Query(default=""),
    health: str = Query(default=""),
    tier: str = Query(default=""),
    enabled: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=0, le=500),
    sort: str = Query(default="priority"),
) -> dict[str, Any]:
    root = get_settings().project_root
    return nodes_ops.list_catalog(
        root,
        q=q,
        health=health,
        tier=tier if tier != "" else None,
        enabled=enabled if enabled != "" else None,
        page=page,
        page_size=page_size,
        sort=sort,
    )


@router.post("/api/nodes")
def post_node(body: NodeAddIn) -> dict[str, Any]:
    root = get_settings().project_root
    return nodes_ops.add_catalog_node(
        root,
        url=body.url,
        label=body.label,
        tags=body.tags,
        tier=body.tier,
        enabled=body.enabled,
    )


@router.delete("/api/nodes/{node_id}")
def delete_node(node_id: str) -> dict[str, Any]:
    root = get_settings().project_root
    return nodes_ops.delete_catalog_node(root, node_id)


@router.patch("/api/nodes/{node_id}")
def patch_node(node_id: str, body: EnabledIn) -> dict[str, Any]:
    root = get_settings().project_root
    return nodes_ops.set_catalog_enabled(root, node_id, body.enabled)


@router.post("/api/nodes/test")
def post_test_nodes(body: NodeTestIn | None = Body(default=None)) -> dict[str, Any]:
    root = get_settings().project_root
    body = body or NodeTestIn()
    return nodes_ops.test_catalog_nodes(
        root,
        ids=body.ids or None,
        timeout=body.timeout,
        limit=body.limit,
    )


@router.get("/api/nodes/clash")
def get_clash_nodes() -> dict[str, Any]:
    root = get_settings().project_root
    return nodes_ops.list_clash_nodes(root)


@router.post("/api/nodes/clash/test")
def post_clash_test(body: ClashTestIn | None = Body(default=None)) -> dict[str, Any]:
    root = get_settings().project_root
    body = body or ClashTestIn()
    return nodes_ops.test_clash_nodes(
        root,
        names=body.names or None,
        timeout_ms=body.timeout_ms,
        limit=body.limit,
    )


@router.post("/api/nodes/clash/import-url")
def post_clash_import_url(body: ClashSubImportIn) -> dict[str, Any]:
    """Fetch a Clash/airport subscription URL and merge into selected pool."""
    root = get_settings().project_root
    return nodes_ops.import_clash_subscription(
        root,
        url=body.url,
        group=body.group,
        groups=body.groups or None,
        prefix=body.prefix,
        mode=body.mode,
        dry_run=body.dry_run,
        reload=body.reload,
        timeout=body.timeout,
        max_proxies=body.max_proxies,
    )


@router.post("/api/nodes/clash/delete-prefix")
def post_clash_delete_prefix(body: ClashDeletePrefixIn) -> dict[str, Any]:
    """Delete Clash proxies whose name is ``prefix`` or starts with ``prefix-``."""
    root = get_settings().project_root
    return nodes_ops.delete_clash_prefix(
        root,
        prefix=body.prefix,
        dry_run=body.dry_run,
        reload=body.reload,
        force=body.force,
    )


@router.post("/api/nodes/clash/prune-unhealthy")
def post_clash_prune_unhealthy(body: ClashPruneUnhealthyIn) -> dict[str, Any]:
    """Delay-test candidates and remove failing proxies from Clash configs."""
    root = get_settings().project_root
    return nodes_ops.prune_clash_unhealthy(
        root,
        prefix=body.prefix,
        group=body.group,
        timeout_ms=body.timeout_ms,
        limit=body.limit,
        dry_run=body.dry_run,
        reload=body.reload,
        delete_defs=body.delete_defs,
    )
