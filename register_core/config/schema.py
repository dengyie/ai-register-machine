"""Register profile schema (register.v1) — pure data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProviderSpec:
    name: str
    options: dict[str, Any] = field(default_factory=dict)

    def outlook_options(self) -> dict[str, Any]:
        if self.name.strip().lower() != "outlook":
            return {}
        options = dict(self.options)
        secret_keys = {
            "password",
            "secret",
            "token",
            "access_token",
            "refresh_token",
            "cookie",
            "cookies",
            "jwt",
            "authorization",
            "proxy",
            "proxy_url",
            "admin_password",
            "admin_password_value",
            "client_secret",
        }

        def find_inline_secrets(value: Any, path: str = "") -> list[str]:
            found: list[str] = []
            if isinstance(value, dict):
                for key, nested in value.items():
                    key_text = str(key)
                    key_path = f"{path}.{key_text}" if path else key_text
                    # Environment-variable *names* (e.g. admin_password_env) are
                    # non-secret config; only exact secret key names are rejected.
                    if key_text.lower() in secret_keys:
                        found.append(key_path)
                    else:
                        found.extend(find_inline_secrets(nested, key_path))
            elif isinstance(value, list):
                for index, nested in enumerate(value):
                    found.extend(find_inline_secrets(nested, f"{path}[{index}]"))
            return found

        inline_secrets = sorted(find_inline_secrets(options))
        if inline_secrets:
            raise ValueError(
                "outlook options cannot contain inline secret values: "
                + ",".join(inline_secrets)
            )
        strategy = int(options.get("captcha_strategy", 2))
        if strategy not in (0, 1, 2):
            raise ValueError("outlook captcha_strategy must be 0, 1, or 2")
        if strategy == 1:
            # Strategy 1 (半自动: pause-for-human via slidex ManualFallbackSession)
            # is accepted by the spec but NOT yet wired in the outlook branch —
            # the adapter would otherwise silently degrade to strategy 0's
            # fully-auto HOLD solve, masking operator intent. Reject loudly so
            # operators get immediate feedback instead of silent mis-behavior.
            # 0 = full-auto hold, 2 = manual handoff only (both implemented).
            raise ValueError(
                "outlook captcha_strategy=1 (ManualFallback) is not implemented "
                "yet; use 0 (full-auto hold) or 2 (manual handoff)"
            )
        options["captcha_strategy"] = strategy
        options["email_suffix"] = str(options.get("email_suffix", "@outlook.com"))
        options["bind_recovery_email"] = bool(options.get("bind_recovery_email", True))
        options["headless"] = bool(options.get("headless", True))
        return options


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
