"""Register profile schema (register.v1) — pure data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProviderSpec:
    name: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MailboxSpec:
    type: str
    domain: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DecodeSpec:
    type: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EgressSpec:
    mode: str = "auto"  # auto | core | clash | list | direct
    proxy: str = ""
    proxy_list: str = ""
    rotate_every: int = 1
    rotate_required: bool = False


@dataclass(slots=True)
class BurnSpec:
    enabled: bool = True
    track: list[str] = field(default_factory=lambda: ["ip", "domain"])
    on_kinds: list[str] = field(
        default_factory=lambda: ["registration_disallowed", "unsupported_email"]
    )
    state_path: str = ""


@dataclass(slots=True)
class StrategySpec:
    fail_fast: bool = True
    fail_fast_kinds: list[str] = field(
        default_factory=lambda: [
            "registration_disallowed",
            "unsupported_email",
            "fatal",
            "verify",
        ]
    )
    egress: EgressSpec = field(default_factory=EgressSpec)
    mail_proxy: str = "direct"  # direct | url | env:NAME
    burn: BurnSpec = field(default_factory=BurnSpec)
    cool_soft_seconds: int = 0


@dataclass(slots=True)
class VerifySpec:
    enabled: bool = True
    name: str = "auto"


@dataclass(slots=True)
class SinkSpec:
    path: str = ""


@dataclass(slots=True)
class SecretsSpec:
    mode: str = "prod"  # dev | prod
    # optional env key overrides (values are env var *names*, not secrets)
    maps: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RegisterProfile:
    """Validated in-memory profile (register.v1)."""

    name: str
    provider: ProviderSpec
    count: int = 1
    mailbox: MailboxSpec | None = None
    decode: DecodeSpec | None = None
    strategy: StrategySpec = field(default_factory=StrategySpec)
    verify: VerifySpec = field(default_factory=VerifySpec)
    sink: SinkSpec = field(default_factory=SinkSpec)
    secrets: SecretsSpec = field(default_factory=SecretsSpec)
    # Legacy paired email_source when mailbox/decode omitted
    email_source: str = ""
    source_path: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def mailbox_type(self) -> str:
        if self.mailbox and self.mailbox.type:
            return self.mailbox.type.strip().lower()
        return (self.email_source or "provider").strip().lower()

    def decode_type(self) -> str:
        if self.decode and self.decode.type:
            return self.decode.type.strip().lower()
        return (self.email_source or "provider").strip().lower()
