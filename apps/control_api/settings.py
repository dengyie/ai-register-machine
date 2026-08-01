"""Environment settings for the control API."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os
import secrets


# Durable secret files under project root (mode 0600 recommended).
SESSION_SECRET_FILENAME = ".control_api_session_secret"
TOKEN_FILENAME = ".control_api_token"

# Hard ceiling on session cookie / token lifetime, independent of the
# operator-tunable CONTROL_API_SESSION_TTL. Even if an operator raises the TTL
# env (it is clamped to [MIN_SESSION_TTL_SECONDS, MAX_SESSION_TTL_SECONDS] in
# get_settings), a signed token can never outlive this bound: read_session_token
# re-validates the ttl baked into the payload against this constant.
MIN_SESSION_TTL_SECONDS = 300
MAX_SESSION_TTL_SECONDS = 7 * 24 * 3600


@dataclass(frozen=True)
class Settings:
    project_root: Path
    host: str
    port: int
    token: str | None
    max_upload_bytes: int = 20 * 1024 * 1024
    # Password / session auth
    session_secret: str | None = None
    session_ttl_seconds: int = 12 * 3600
    cookie_secure: bool = False
    password_login_enabled: bool = True
    bootstrap_user: str | None = None
    bootstrap_password: str | None = None
    # Where session_secret / token came from (for ops logs only; never the value)
    session_secret_source: str = "missing"
    token_source: str = "missing"

    @property
    def auth_required(self) -> bool:
        """True when bearer token is set. Password users checked via auth_is_required()."""
        return bool(self.token)


def _read_secret_file(path: Path) -> str | None:
    """Read one-line secret file; return stripped text or None. Never log contents."""
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        # first non-empty line only
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
        return text or None
    except Exception:
        return None


def _resolve_project_root() -> Path:
    root = os.environ.get("REGISTER_PROJECT_ROOT")
    return Path(root).resolve() if root else Path.cwd().resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    project_root = _resolve_project_root()
    host = os.environ.get("CONTROL_API_HOST", "127.0.0.1")
    port = int(os.environ.get("CONTROL_API_PORT", "8787"))
    max_upload = int(os.environ.get("CONTROL_API_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

    # ── bearer token: env → file ────────────────────────────────────────────
    token = os.environ.get("CONTROL_API_TOKEN")
    token_source = "env"
    if token is not None and token.strip() == "":
        token = None
    if not token:
        file_token = _read_secret_file(project_root / TOKEN_FILENAME)
        if file_token:
            token = file_token
            token_source = f"file:{TOKEN_FILENAME}"
        else:
            token_source = "missing"

    # ── session secret: env → file → token → dev env → ephemeral ────────────
    session_secret = os.environ.get("CONTROL_API_SESSION_SECRET")
    session_secret_source = "env"
    if session_secret is not None and session_secret.strip() == "":
        session_secret = None
    if not session_secret:
        file_secret = _read_secret_file(project_root / SESSION_SECRET_FILENAME)
        if file_secret:
            session_secret = file_secret
            session_secret_source = f"file:{SESSION_SECRET_FILENAME}"
    if not session_secret and token:
        session_secret = token
        session_secret_source = "token"
    if not session_secret:
        dev = os.environ.get("CONTROL_API_DEV_SESSION_SECRET")
        if dev and dev.strip():
            session_secret = dev.strip()
            session_secret_source = "env:CONTROL_API_DEV_SESSION_SECRET"
    if not session_secret and os.environ.get("CONTROL_API_ALLOW_EPHEMERAL_SESSION") == "1":
        session_secret = secrets.token_hex(32)
        session_secret_source = "ephemeral"
    if not session_secret:
        session_secret_source = "missing"

    ttl = int(os.environ.get("CONTROL_API_SESSION_TTL", str(12 * 3600)))
    # Clamp to a safe band: never below the min floor (5 min) and never above the
    # hard ceiling (7 days). The cookie max_age AND the signed-token ttl both
    # derive from this, so a runaway TTL env can't mint effectively-immortal
    # sessions. read_session_token additionally re-validates the ttl in the
    # payload against MAX_SESSION_TTL_SECONDS, so a token minted before a
    # re-clamp can't slip a longer-lived ttl through either.
    ttl = max(MIN_SESSION_TTL_SECONDS, min(MAX_SESSION_TTL_SECONDS, ttl))
    cookie_secure = os.environ.get("CONTROL_API_COOKIE_SECURE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    # Password login on by default; set CONTROL_API_PASSWORD_LOGIN=0 to disable
    pw_flag = os.environ.get("CONTROL_API_PASSWORD_LOGIN", "1").strip().lower()
    password_login_enabled = pw_flag not in {"0", "false", "no", "off"}

    # Empty user store only: first-start default operator. Override via env.
    # Production hosts that already have .control_api_users.json are untouched
    # (ensure_bootstrap_user is a no-op when any user exists).
    bootstrap_user = os.environ.get("CONTROL_API_BOOTSTRAP_USER")
    bootstrap_password = os.environ.get("CONTROL_API_BOOTSTRAP_PASSWORD")
    if bootstrap_user is not None and bootstrap_user.strip() == "":
        bootstrap_user = None
    if bootstrap_password is not None and bootstrap_password.strip() == "":
        bootstrap_password = None
    if bootstrap_user is None and "CONTROL_API_BOOTSTRAP_USER" not in os.environ:
        bootstrap_user = "admin"
    if bootstrap_password is None and "CONTROL_API_BOOTSTRAP_PASSWORD" not in os.environ:
        bootstrap_password = "admin123"

    return Settings(
        project_root=project_root,
        host=host,
        port=port,
        token=token,
        max_upload_bytes=max_upload,
        session_secret=session_secret,
        session_ttl_seconds=ttl,
        cookie_secure=cookie_secure,
        password_login_enabled=password_login_enabled,
        bootstrap_user=bootstrap_user,
        bootstrap_password=bootstrap_password,
        session_secret_source=session_secret_source,
        token_source=token_source,
    )


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def log_auth_startup(settings: Settings | None = None) -> None:
    """Ops-safe startup line: sources + readiness, never secret values."""
    s = settings or get_settings()
    secret_ok = bool(s.session_secret)
    token_ok = bool(s.token)
    users = "yes" if (s.project_root / ".control_api_users.json").is_file() else "no"
    print(
        "[control_api] auth "
        f"session_secret={s.session_secret_source} "
        f"token={s.token_source} "
        f"password_login={'on' if s.password_login_enabled else 'off'} "
        f"users_file={users} "
        f"ready={'yes' if (secret_ok and s.password_login_enabled) or token_ok else 'NO'}",
        flush=True,
    )
    if s.password_login_enabled and not secret_ok:
        print(
            "[control_api] WARN password login will fail: session secret missing. "
            f"Set CONTROL_API_SESSION_SECRET or create {s.project_root / SESSION_SECRET_FILENAME} "
            f"(or set CONTROL_API_TOKEN / {TOKEN_FILENAME}). "
            "Bare `python -m apps.control_api` without secrets is unsupported on production.",
            flush=True,
        )
    if s.session_secret_source == "ephemeral":
        print(
            "[control_api] WARN session secret is ephemeral — logins invalidate on restart. "
            f"Persist via {SESSION_SECRET_FILENAME} or CONTROL_API_SESSION_SECRET.",
            flush=True,
        )
