from __future__ import annotations

from typing import Any

from register_core.contracts import RegisterResult


class OutlookProvider:
    name = "outlook"

    def __init__(self, *, config: dict[str, Any] | None = None, **options: Any) -> None:
        self.config = {**(config or {}), **options}

    def register_one(self, *, email_source=None, extra=None) -> RegisterResult:
        return RegisterResult(
            ok=False,
            provider=self.name,
            error="outlook_components_unavailable",
            error_kind="provider",
            secret_kind="none",
        )
