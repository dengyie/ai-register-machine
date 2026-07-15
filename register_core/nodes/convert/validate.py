"""Validate Clash-style proxy dicts (legality, not liveness)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from register_core.nodes.convert.types import DIALABLE_TYPES, PROTOCOL_TYPES, REQUIRED_BY_TYPE


@dataclass
class ValidationIssue:
    level: str  # error | warn
    code: str
    message: str
    name: str = ""
    index: int = -1

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "name": self.name,
            "index": self.index,
        }


def _port_ok(port: Any) -> bool:
    try:
        p = int(port)
    except (TypeError, ValueError):
        return False
    return 1 <= p <= 65535


def validate_proxy(proxy: dict[str, Any], *, index: int = -1) -> list[ValidationIssue]:
    """Return issues for one proxy dict. Empty list ⇒ legal enough to pack."""
    issues: list[ValidationIssue] = []
    if not isinstance(proxy, dict):
        return [
            ValidationIssue(
                level="error",
                code="not_object",
                message="proxy entry is not a mapping",
                index=index,
            )
        ]
    name = str(proxy.get("name") or "").strip()
    ptype = str(proxy.get("type") or "").lower().strip()
    if not ptype:
        issues.append(
            ValidationIssue(
                level="error",
                code="missing_type",
                message="missing type",
                name=name,
                index=index,
            )
        )
        return issues
    if ptype not in DIALABLE_TYPES and ptype not in PROTOCOL_TYPES and ptype not in REQUIRED_BY_TYPE:
        issues.append(
            ValidationIssue(
                level="warn",
                code="unknown_type",
                message=f"unknown type {ptype!r} — will still pack for core if name/server/port ok",
                name=name,
                index=index,
            )
        )
    if not name:
        issues.append(
            ValidationIssue(
                level="error",
                code="missing_name",
                message="missing name",
                name=name,
                index=index,
            )
        )
    required = REQUIRED_BY_TYPE.get(ptype, ("server", "port"))
    for key in required:
        val = proxy.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            issues.append(
                ValidationIssue(
                    level="error",
                    code=f"missing_{key.replace('-', '_')}",
                    message=f"missing required field {key!r} for type {ptype}",
                    name=name,
                    index=index,
                )
            )
    if "port" in required or "port" in proxy:
        if proxy.get("port") is not None and not _port_ok(proxy.get("port")):
            issues.append(
                ValidationIssue(
                    level="error",
                    code="bad_port",
                    message=f"invalid port {proxy.get('port')!r}",
                    name=name,
                    index=index,
                )
            )
    server = str(proxy.get("server") or "").strip()
    if server and (" " in server or server.startswith("/")):
        issues.append(
            ValidationIssue(
                level="error",
                code="bad_server",
                message=f"invalid server host {server!r}",
                name=name,
                index=index,
            )
        )
    return issues


def validate_proxy_list(proxies: list[Any]) -> tuple[list[dict[str, Any]], list[ValidationIssue]]:
    """Split into valid proxy dicts and all issues (including rejected)."""
    ok: list[dict[str, Any]] = []
    issues: list[ValidationIssue] = []
    if not isinstance(proxies, list):
        return [], [
            ValidationIssue(
                level="error",
                code="not_list",
                message="proxies is not a list",
            )
        ]
    for i, item in enumerate(proxies):
        item_issues = validate_proxy(item if isinstance(item, dict) else item, index=i)
        issues.extend(item_issues)
        errors = [x for x in item_issues if x.level == "error"]
        if errors:
            continue
        if isinstance(item, dict):
            ok.append(item)
    return ok, issues


@dataclass
class ProfileReport:
    source: str
    format: str
    total: int = 0
    accepted: int = 0
    rejected: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "format": self.format,
            "total": self.total,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "issues": [i.to_dict() for i in self.issues[:50]],
            "issue_count": len(self.issues),
        }
