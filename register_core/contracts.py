"""Shared data contracts between layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

# Public error_kind taxonomy for sinks / strategy / ops dashboards.
# Keep in sync with strategy defaults, proxy quarantine exclusions, and
# providers/chatgpt/protocol/flow.py step mapping.
ALLOWED_ERROR_KINDS: frozenset[str] = frozenset(
    {
        # Mail / OTP delivery
        "mail_miss",
        # Product / risk engine
        "registration_disallowed",
        "unsupported_email",
        "already_registered",
        # Anti-bot / captcha / sentinel PoW
        "captcha",
        # Protocol stages
        "session",
        "otp_invalid",
        "oauth_callback",
        "token",
        # Infra
        "proxy",
        "network",
        # Generic / terminal
        "provider",
        "verify",
        "fatal",
        "other",
    }
)

# Provider-local / historical aliases → public kind.
_ERROR_KIND_ALIASES: dict[str, str] = {
    "disallowed": "registration_disallowed",
    "registration-disallowed": "registration_disallowed",
    "sentinel": "captcha",
    "sentinel_fail": "captcha",
    "pow": "captcha",
    "otp_bad": "otp_invalid",
    "bad_otp": "otp_invalid",
    "invalid_otp": "otp_invalid",
    "oauth": "oauth_callback",
    "callback": "oauth_callback",
    "missing_oauth_callback": "oauth_callback",
    "refresh": "token",
    "missing_refresh": "token",
    "missing_tokens": "token",
    "oauth_token": "token",
}


def normalize_error_kind(kind: str | None) -> str:
    """Map provider-reported kind into the public taxonomy; unknown → provider."""
    k = (kind or "").strip().lower().replace(" ", "_")
    if not k:
        return "provider"
    if k in ALLOWED_ERROR_KINDS:
        return k
    aliased = _ERROR_KIND_ALIASES.get(k)
    if aliased is not None:
        return aliased
    # Prefix soft-match for compound provider tags (e.g. oauth_token_http_400).
    for prefix, target in (
        ("registration_disallowed", "registration_disallowed"),
        ("unsupported_email", "unsupported_email"),
        ("already_registered", "already_registered"),
        ("otp_invalid", "otp_invalid"),
        ("oauth_callback", "oauth_callback"),
        ("oauth_token", "token"),
        ("missing_token", "token"),
        ("sentinel", "captcha"),
        ("captcha", "captcha"),
        ("mail_miss", "mail_miss"),
        ("session", "session"),
    ):
        if k == prefix or k.startswith(prefix + "_") or k.startswith(prefix + ":"):
            return target
    return "provider"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _preview_secret(value: str) -> str:
    s = value or ""
    if not s:
        return ""
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}…{s[-4:]}(len={len(s)})"


@dataclass(slots=True)
class Mailbox:
    """One mailbox allocated for a single registration attempt."""

    address: str
    token: str = ""
    password: str = ""
    provider: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def redact(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "provider": self.provider,
            "has_token": bool(self.token),
            "meta_keys": sorted(self.meta.keys()),
        }


@dataclass(slots=True)
class OtpCode:
    code: str
    source: str = ""
    received_at: datetime = field(default_factory=_utcnow)
    raw_subject: str = ""


@dataclass(slots=True)
class OtpWaitDiagnostics:
    """Observability for one OTP poll window (no raw MIME/token)."""

    poll_count: int = 0
    message_scan_count: int = 0
    empty_rounds: int = 0
    elapsed_seconds: float = 0.0
    timeout_s: float = 0.0
    first_message_seen_at: float | None = None
    matched_at: float | None = None
    first_seen_after_seconds: float | None = None
    matched_after_seconds: float | None = None
    abort_reason: str = ""
    failure_class: str = ""  # no_mail | parse_fail | stale_code | imap_error | aborted | ""
    provider: str = ""
    sender_hint: str = ""
    notes: str = ""


@dataclass(slots=True)
class RegisterJob:
    """Input to one pipeline run."""

    provider: str
    count: int = 1
    email_source: str = "provider"
    verify: bool = True
    fail_fast: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RegisterResult:
    """Normalized output of a provider registration."""

    ok: bool
    provider: str
    email: str = ""
    password: str = ""
    secret: str = ""  # API key / SSO cookie / token — never log full in public
    secret_kind: str = ""  # api_key | sso | refresh_token | none | pending
    error: str = ""
    error_kind: str = ""  # see ALLOWED_ERROR_KINDS / normalize_error_kind
    artifacts: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)

    def secret_preview(self) -> str:
        return _preview_secret(self.secret)

    def to_public_dict(self) -> dict[str, Any]:
        """Safe for stdout / shared logs — no passwords, secrets, or raw tails."""
        return {
            "ok": self.ok,
            "provider": self.provider,
            "email": self.email,
            "password": _preview_secret(self.password) if self.password else "",
            "secret": self.secret_preview(),
            "secret_kind": self.secret_kind,
            "error": self.error,
            "error_kind": self.error_kind,
            "artifacts": _public_artifacts(self.artifacts),
            "created_at": self.created_at.isoformat(),
        }

    def to_sink_dict(self) -> dict[str, Any]:
        """Full record for private sinks (restricted file perms)."""
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        # still scrub obvious OTP tails inside artifacts if present
        arts = dict(self.artifacts or {})
        if "tail" in arts and isinstance(arts["tail"], str):
            from register_core.util.process import redact_log_tail

            arts["tail"] = redact_log_tail(arts["tail"])
        d["artifacts"] = arts
        return d


@dataclass(slots=True)
class VerifyResult:
    ok: bool
    provider: str
    capability: str = ""
    detail: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _public_artifacts(artifacts: dict[str, Any] | None) -> dict[str, Any]:
    if not artifacts:
        return {}
    allow = {
        "runtime",
        "keys_path",
        "accounts_path",
        "auth_path",
        "note",
        "exit_code",
        "timed_out",
        "ledger",
        "email_source",
        "has_id_token",
        "steps",
        "step_keys",
        "fail_step",
        "protocol",
        "strategy_burn",
        "otp_wait",
        "mail_proxy",
        "register_proxy",
        "proxy",
        "proxy_mode",
        "proxy_label",
        "mailbox_provider",
        "device_id",
    }
    out: dict[str, Any] = {}
    for k, v in artifacts.items():
        if k in allow:
            out[k] = v
        elif k == "tail" and isinstance(v, str):
            from register_core.util.process import redact_log_tail

            out["tail_redacted"] = redact_log_tail(v, limit=400)
    return out
