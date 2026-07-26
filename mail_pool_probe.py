"""Mail credential pool probe: load, sample, OAuth refresh, classify, quarantine.

Import-safe for FastAPI and CLI. Does NOT import grok_register_ttk (GUI/browser side effects).
OAuth endpoints mirror HOTMAIL_TOKEN_ENDPOINTS in grok_register_ttk.py.

Security: never log or return password / client_id / refresh_token.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

KNOWN_DOMAINS: list[str] = [
    "hotmail.com",
    "outlook.com",
    "live.com",
    "msn.com",
]

# Same endpoint/scope pairs as grok_register_ttk.HOTMAIL_TOKEN_ENDPOINTS
TOKEN_ENDPOINTS: list[tuple[str, dict[str, str]]] = [
    (
        "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        {"scope": "offline_access https://outlook.office.com/IMAP.AccessAsUser.All"},
    ),
    (
        "https://login.live.com/oauth20_token.srf",
        {"scope": "offline_access https://outlook.office.com/IMAP.AccessAsUser.All"},
    ),
    ("https://login.live.com/oauth20_token.srf", {}),
    (
        "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        {
            "scope": (
                "offline_access https://graph.microsoft.com/Mail.Read "
                "https://graph.microsoft.com/User.Read"
            )
        },
    ),
]

STATUS_OK = "ok"
STATUS_GRANT_EXPIRED = "grant_expired"
STATUS_REFRESH_INVALID = "refresh_invalid"
STATUS_ABUSE_MODE = "abuse_mode"
STATUS_NETWORK_ERROR = "network_error"
STATUS_PARSE_ERROR = "parse_error"
STATUS_UNKNOWN = "unknown"

# Safe to physically remove from live pool (credential truly dead at IdP).
# network_error / unknown / parse_error are NOT auto-quarantinable.
QUARANTINABLE_STATUSES = frozenset(
    {
        STATUS_GRANT_EXPIRED,
        STATUS_REFRESH_INVALID,
        STATUS_ABUSE_MODE,
    }
)

# Transient / inconclusive — show as 挂了-ish but do not default-select for remove.
SOFT_FAIL_STATUSES = frozenset(
    {
        STATUS_NETWORK_ERROR,
        STATUS_UNKNOWN,
        STATUS_PARSE_ERROR,
    }
)

DEAD_STATUSES = QUARANTINABLE_STATUSES | SOFT_FAIL_STATUSES

_MS_ERROR_MAX = 200
_DEFAULT_DEAD_NAME = "mail_credentials.dead.txt"
_DEFAULT_PER_ACCOUNT_TIMEOUT = 20.0
_DEFAULT_WALL_SECONDS = 90.0
# terminal IdP errors: stop trying remaining endpoints for this account
_TERMINAL_ERROR_RE = re.compile(
    r"aadsts\d+|invalid_grant|unauthorized_client|invalid_client|"
    r"interaction_required|abuse\s*mode|grant\s+is\s+expired|"
    r"expired\s+or\s+revoked",
    re.I,
)

_file_lock = threading.Lock()
_probe_global_lock = threading.Lock()  # at most one in-process probe wave


@dataclass
class Credential:
    email: str
    password: str
    client_id: str
    refresh_token: str
    line_no: int = 0
    raw_line: str = ""

    @property
    def domain(self) -> str:
        if "@" not in self.email:
            return ""
        return self.email.rsplit("@", 1)[-1].strip().lower()


@dataclass
class ProbeResult:
    email: str
    domain: str
    status: str
    reason: str = ""
    ms_error: str = ""
    quarantinable: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "domain": self.domain,
            "status": self.status,
            "reason": self.reason or "",
            "ms_error": self.ms_error or "",
            "quarantinable": bool(self.quarantinable),
        }


RefreshFn = Callable[[Credential], tuple[bool, str, str | None]]
# return: (ok, error_message, new_refresh_token_or_None)


def is_quarantinable(status: str) -> bool:
    return str(status or "") in QUARANTINABLE_STATUSES


# Separators vendors use for one-line credential dumps (longest first).
_CRED_SEPS: tuple[str, ...] = ("----", "||||", "|||", "||", "|", "\t", ";", ",")

# JSON wrapper keys that hold a list/object of accounts
_JSON_LIST_KEYS: tuple[str, ...] = (
    "accounts",
    "account",
    "data",
    "list",
    "items",
    "result",
    "results",
    "records",
    "rows",
    "emails",
    "mails",
    "credentials",
    "creds",
    "payload",
)

# CSV / header aliases → canonical field
_HEADER_ALIASES: dict[str, str] = {
    "email": "email",
    "mail": "email",
    "e-mail": "email",
    "account": "email",
    "username": "email",
    "user": "email",
    "login": "email",
    "password": "password",
    "pass": "password",
    "pwd": "password",
    "passwd": "password",
    "clientid": "client_id",
    "client_id": "client_id",
    "client-id": "client_id",
    "cid": "client_id",
    "appid": "client_id",
    "app_id": "client_id",
    "application_id": "client_id",
    "refreshtoken": "refresh_token",
    "refresh_token": "refresh_token",
    "refresh-token": "refresh_token",
    "token": "refresh_token",
    "refresh": "refresh_token",
    "rt": "refresh_token",
    "oauth_token": "refresh_token",
    "oauthtoken": "refresh_token",
    "ms_token": "refresh_token",
}


def _pick_json_field(obj: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in obj and obj[name] is not None:
            val = obj[name]
            if isinstance(val, (dict, list)):
                continue
            return str(val).strip()
    # case-insensitive fallback
    lower = {str(k).lower(): v for k, v in obj.items()}
    for name in names:
        v = lower.get(name.lower())
        if v is not None and not isinstance(v, (dict, list)):
            return str(v).strip()
    return ""


def format_credential_line(
    email: str,
    password: str,
    client_id: str,
    refresh_token: str,
) -> str:
    """Canonical live-pool line (no trailing newline)."""
    return f"{email}----{password}----{client_id}----{refresh_token}"


def _fields_from_parts(parts: list[str]) -> dict[str, str] | None:
    """Validate 4-tuple email/password/client_id/refresh_token."""
    if len(parts) < 4:
        return None
    email_addr = parts[0].strip().strip('"').strip("'")
    password = parts[1].strip().strip('"').strip("'")
    client_id = parts[2].strip().strip('"').strip("'")
    refresh_token = parts[3].strip().strip('"').strip("'")
    # dead-archive may append ----reason----ts inside token field
    if "----" in refresh_token:
        refresh_token = refresh_token.split("----", 1)[0].strip()
    if not email_addr or "@" not in email_addr or not client_id or not refresh_token:
        return None
    return {
        "email": email_addr,
        "password": password,
        "client_id": client_id,
        "refresh_token": refresh_token,
    }


def parse_json_credential_obj(obj: Any) -> dict[str, str] | None:
    """Vendor JSON object → credential fields, or None if incomplete.

    Handles flat keys and mild nesting:
    ``{"oauth":{"refresh_token":…}}``, ``{"credentials":{…}}``.
    """
    if not isinstance(obj, dict):
        return None

    # Flatten one level of common nested bags
    bags: list[dict[str, Any]] = [obj]
    for key in (
        "credentials",
        "credential",
        "oauth",
        "auth",
        "token",
        "tokens",
        "account",
        "user",
        "data",
        "info",
    ):
        nested = obj.get(key)
        if isinstance(nested, dict):
            bags.append(nested)

    email_addr = password = client_id = refresh_token = ""
    for bag in bags:
        if not email_addr:
            email_addr = _pick_json_field(
                bag, "email", "mail", "account", "username", "user", "login", "Email"
            )
        if not password:
            password = _pick_json_field(bag, "password", "pass", "pwd", "passwd", "Password")
        if not client_id:
            client_id = _pick_json_field(
                bag,
                "clientId",
                "client_id",
                "clientid",
                "cid",
                "appId",
                "app_id",
                "application_id",
                "ClientId",
            )
        if not refresh_token:
            refresh_token = _pick_json_field(
                bag,
                "refreshToken",
                "refresh_token",
                "token",
                "refresh",
                "rt",
                "oauth_token",
                "ms_token",
                "RefreshToken",
            )

    # Sometimes email is the only top-level identity and rest nested already tried
    if not email_addr or "@" not in email_addr or not client_id or not refresh_token:
        return None
    return {
        "email": email_addr,
        "password": password,
        "client_id": client_id,
        "refresh_token": refresh_token,
    }


def _iter_json_credential_objs(data: Any) -> list[Any]:
    """Unwrap vendor JSON envelopes into a list of candidate account objects."""
    if data is None:
        return []
    if isinstance(data, list):
        return list(data)
    if not isinstance(data, dict):
        return []
    # Direct account object
    if parse_json_credential_obj(data) is not None:
        return [data]
    # Wrapped list/object under known keys
    for key in _JSON_LIST_KEYS:
        if key not in data:
            # case-insensitive
            lower_map = {str(k).lower(): k for k in data.keys()}
            real = lower_map.get(key.lower())
            if real is None:
                continue
            val = data[real]
        else:
            val = data[key]
        if isinstance(val, list):
            return list(val)
        if isinstance(val, dict):
            # single account under account/data
            return [val]
    return [data]


def _split_delimited_line(s: str) -> list[str] | None:
    """Try known separators; return 4+ parts or None."""
    # Prefer multi-char seps first (---- before -)
    for sep in _CRED_SEPS:
        if sep not in s:
            continue
        if sep == ",":
            # CSV: only if looks like email,... and >=3 commas OR quoted fields
            if s.count("@") != 1:
                continue
        parts = s.split(sep)
        # allow >4 (extra junk after token) — take first 4, join rest into token
        if len(parts) < 4:
            continue
        if len(parts) > 4 and sep in ("----", "|", "\t", ";"):
            head = [p.strip() for p in parts[:3]]
            tail = sep.join(parts[3:]).strip()
            parts = head + [tail]
        elif len(parts) > 4 and sep == ",":
            # CSV with commas inside token — can't safely join; require exactly 4
            if len(parts) != 4:
                continue
        parts = [p.strip() for p in parts[:4]]
        if _fields_from_parts(parts):
            return parts
    # colon form: email:pass:uuid:token (token may contain ':' rarely — maxsplit 3)
    if s.count(":") >= 3 and "@" in s.split(":", 1)[0]:
        parts = s.split(":", 3)
        if _fields_from_parts(parts):
            return [p.strip() for p in parts]
    # whitespace-separated (email pass client_id refresh_token) — 4 fields, email first
    if "@" in s and "----" not in s and "|" not in s:
        ws = s.split()
        if len(ws) == 4 and _fields_from_parts(ws):
            return ws
    return None


def _parse_csv_block(lines: list[str]) -> tuple[list[str], dict[str, int]] | None:
    """If first non-empty line is a header row, parse remaining as CSV rows."""
    import csv
    from io import StringIO

    nonempty = [ln for ln in lines if ln.strip() and not ln.strip().startswith(("#", "//"))]
    if len(nonempty) < 2:
        return None
    header_raw = nonempty[0].strip().lstrip("﻿")
    # Must look like a header, not an email line
    if "@" in header_raw.split(",")[0]:
        return None
    try:
        reader = csv.reader(StringIO("\n".join(nonempty)))
        rows = list(reader)
    except csv.Error:
        return None
    if not rows:
        return None
    headers = [re.sub(r"[^a-z0-9_-]", "", h.strip().lower().replace(" ", "_")) for h in rows[0]]
    # map headers
    colmap: dict[str, int] = {}
    for i, h in enumerate(headers):
        canon = _HEADER_ALIASES.get(h)
        if canon and canon not in colmap:
            colmap[canon] = i
    if "email" not in colmap or "client_id" not in colmap or "refresh_token" not in colmap:
        return None
    out: list[str] = []
    stats = {
        "input_lines": len(rows) - 1,
        "written": 0,
        "skipped": 0,
        "json_objects": 0,
        "csv_rows": 0,
    }
    for row in rows[1:]:
        if not row or all(not str(c).strip() for c in row):
            stats["skipped"] += 1
            continue

        def cell(name: str) -> str:
            idx = colmap.get(name)
            if idx is None or idx >= len(row):
                return ""
            return str(row[idx]).strip()

        fields = _fields_from_parts(
            [cell("email"), cell("password"), cell("client_id"), cell("refresh_token")]
        )
        if not fields:
            stats["skipped"] += 1
            continue
        out.append(
            format_credential_line(
                fields["email"],
                fields["password"],
                fields["client_id"],
                fields["refresh_token"],
            )
        )
        stats["written"] += 1
        stats["csv_rows"] += 1
    return out, stats


def normalize_credential_line(line: str) -> str | None:
    """Normalize one input line to canonical ``email----…----token`` or None.

    Accepts:
    - ``email----password----client_id----refresh_token`` (canonical)
    - ``|`` / ``;`` / tab / ``,`` / ``:`` separated 4-field lines
    - vendor JSON object line: ``{"email","password","clientId","refreshToken"}``
    - a JSON array of such objects (returns first valid only — use
      :func:`normalize_credential_text` for multi)
    """
    raw = line.rstrip("\n").lstrip("﻿")
    s = raw.strip()
    if not s or s.lstrip().startswith(("#", "//")):
        return None
    if s.startswith("{") or s.startswith("["):
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return None
        for item in _iter_json_credential_objs(data):
            fields = parse_json_credential_obj(item)
            if fields:
                return format_credential_line(
                    fields["email"],
                    fields["password"],
                    fields["client_id"],
                    fields["refresh_token"],
                )
        return None
    parts = _split_delimited_line(s)
    if not parts:
        return None
    fields = _fields_from_parts(parts)
    if not fields:
        return None
    return format_credential_line(
        fields["email"],
        fields["password"],
        fields["client_id"],
        fields["refresh_token"],
    )


def normalize_credential_text(content: str) -> tuple[str, dict[str, int]]:
    """Normalize multi-line / JSON / CSV import text to canonical pool lines.

    Returns ``(text_with_trailing_newline, stats)``.
    ``stats`` keys: ``input_lines``, ``written``, ``skipped``, ``json_objects``
    (plus ``csv_rows`` when CSV header path used).
    """
    text = (content or "").lstrip("﻿")
    stripped = text.strip()
    stats: dict[str, int] = {
        "input_lines": 0,
        "written": 0,
        "skipped": 0,
        "json_objects": 0,
    }
    out: list[str] = []

    # Whole-body JSON array / object (common paste from vendors, pretty-printed OK)
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if data is not None:
            items = _iter_json_credential_objs(data)
            stats["input_lines"] = max(len(items), 1)
            for item in items:
                fields = parse_json_credential_obj(item)
                if not fields:
                    stats["skipped"] += 1
                    continue
                out.append(
                    format_credential_line(
                        fields["email"],
                        fields["password"],
                        fields["client_id"],
                        fields["refresh_token"],
                    )
                )
                stats["written"] += 1
                stats["json_objects"] += 1
            body = "\n".join(out)
            if body and not body.endswith("\n"):
                body += "\n"
            return body, stats

    # CSV with header row
    lines = text.splitlines()
    csv_hit = _parse_csv_block(lines)
    if csv_hit is not None:
        rows, csv_stats = csv_hit
        if csv_stats.get("written", 0) > 0 or csv_stats.get("input_lines", 0) > 0:
            body = "\n".join(rows)
            if body and not body.endswith("\n"):
                body += "\n"
            return body, csv_stats

    for raw in lines:
        stats["input_lines"] += 1
        s = raw.strip()
        if not s or s.lstrip().startswith(("#", "//")):
            if s.startswith(("#", "//")):
                out.append(s)
            else:
                stats["skipped"] += 1
            continue
        was_json = s.startswith("{") or s.startswith("[")
        norm = normalize_credential_line(raw)
        if not norm:
            stats["skipped"] += 1
            continue
        out.append(norm)
        stats["written"] += 1
        if was_json:
            stats["json_objects"] += 1
    body = "\n".join(out)
    if body and not body.endswith("\n"):
        body += "\n"
    return body, stats


def parse_credential_line(line: str, line_no: int = 0) -> Credential | None:
    """Parse dash form or vendor JSON object line into a Credential."""
    raw = line.rstrip("\n")
    if not raw.strip() or raw.lstrip().startswith(("#", "//")):
        return None
    # JSON object / one-element handling via normalize
    if raw.strip().startswith("{") or raw.strip().startswith("["):
        norm = normalize_credential_line(raw)
        if not norm:
            return None
        parts = norm.split("----", 3)
        return Credential(
            email=parts[0],
            password=parts[1],
            client_id=parts[2],
            refresh_token=parts[3],
            line_no=line_no,
            raw_line=raw if raw.endswith("\n") else raw + "\n",
        )
    # maxsplit=3 keeps refresh_token intact even if it contains ---- (unlikely)
    # dead-archive 6-field lines: extra reason/ts stay inside field 4 → still parseable
    # for identity; refresh_token may be dirty if reading dead file as live — don't.
    parts = raw.split("----", 3)
    if len(parts) < 4:
        return None
    email_addr = parts[0].strip()
    password = parts[1].strip()
    client_id = parts[2].strip()
    # If line is dead-archive form, field 3 may contain refresh----reason----ts;
    # live pool lines have exactly 4 fields. Strip trailing reason/ts only when
    # there are clearly extra ---- segments after a normal token.
    refresh_token = parts[3].strip()
    # Prefer first segment of field 3 as token when 6-field dead line is fed in.
    if "----" in refresh_token:
        # dead line re-parsed: take first chunk as token (best-effort identity only)
        refresh_token = refresh_token.split("----", 1)[0].strip()
    if not email_addr or "@" not in email_addr or not client_id or not refresh_token:
        return None
    return Credential(
        email=email_addr,
        password=password,
        client_id=client_id,
        refresh_token=refresh_token,
        line_no=line_no,
        raw_line=raw if raw.endswith("\n") else raw + "\n",
    )


def resolve_pool_path(
    root: Path | str | None = None,
    *,
    config_path: str | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Resolve live pool path: env HOTMAIL_ACCOUNTS_FILE > config > default."""
    env = env if env is not None else os.environ
    env_path = (env.get("HOTMAIL_ACCOUNTS_FILE") or "").strip()
    raw = env_path or (config_path or "").strip() or "mail_credentials.txt"
    p = Path(os.path.expanduser(raw))
    if p.is_absolute():
        return p
    base = Path(root) if root is not None else Path.cwd()
    return (base / p).resolve()


def dead_archive_path(live_path: Path | str) -> Path:
    live = Path(live_path)
    return live.with_name(_DEFAULT_DEAD_NAME)


def load_pool(path: Path | str) -> list[Credential]:
    """Load pool file, dedupe by lowercased email (first wins)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"mail credentials file not found: {p}")
    accounts: list[Credential] = []
    seen: set[str] = set()
    text = p.read_text(encoding="utf-8-sig")
    for line_no, raw in enumerate(text.splitlines(keepends=True), 1):
        item = parse_credential_line(raw, line_no=line_no)
        if not item:
            continue
        key = item.email.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        accounts.append(item)
    return accounts


def scan_pool_file(path: Path | str) -> dict[str, Any]:
    """Scan live file for raw/unique/duplicate/invalid counts (no secrets)."""
    p = Path(path)
    if not p.is_file():
        return {
            "raw_lines": 0,
            "parsed_lines": 0,
            "unique": 0,
            "duplicate_extra": 0,
            "invalid_lines": 0,
            "comment_or_blank": 0,
            "needs_compact": False,
        }
    raw_lines = 0
    parsed_lines = 0
    invalid_lines = 0
    comment_or_blank = 0
    seen: set[str] = set()
    duplicate_extra = 0
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            comment_or_blank += 1
            continue
        if s.startswith(("#", "//")):
            comment_or_blank += 1
            continue
        raw_lines += 1
        item = parse_credential_line(raw)
        if not item:
            invalid_lines += 1
            continue
        parsed_lines += 1
        key = item.email.strip().lower()
        if key in seen:
            duplicate_extra += 1
            continue
        seen.add(key)
    unique = len(seen)
    return {
        "raw_lines": raw_lines,
        "parsed_lines": parsed_lines,
        "unique": unique,
        "duplicate_extra": duplicate_extra,
        "invalid_lines": invalid_lines,
        "comment_or_blank": comment_or_blank,
        "needs_compact": bool(duplicate_extra or invalid_lines),
    }


def compact_pool(
    live_path: Path | str,
    *,
    dry_run: bool = False,
    drop_invalid: bool = True,
    drop_comments: bool = False,
) -> dict[str, Any]:
    """Rewrite live pool: keep first line per email, drop dups (and optional junk).

    - First occurrence wins (same rule as ``load_pool``).
    - Surviving credentials are rewritten in canonical
      ``email----password----clientId----refreshToken`` form.
    - ``drop_invalid`` (default True): remove non-comment unparseable lines.
    - ``drop_comments`` (default False): keep ``#`` / blank lines in place order
      only when they appear before the first dropped region is not attempted;
      when True, strip all comments/blanks for a pure credential file.
    - Always backups before write (unless dry_run or nothing changes).
    - Never returns secrets.
    """
    live = Path(live_path)
    if not live.is_file():
        raise FileNotFoundError(f"mail credentials file not found: {live}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    with _file_lock:
        text = live.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.splitlines(keepends=True)
        keep: list[str] = []
        seen: set[str] = set()
        kept_emails: list[str] = []
        before_parsed = 0
        duplicate_extra = 0
        invalid_dropped = 0
        comments_kept = 0
        comments_dropped = 0

        for raw in lines:
            body = raw[:-1] if raw.endswith("\n") else raw
            if body.endswith("\r"):
                body = body[:-1]
            s = body.strip()
            if not s or s.startswith(("#", "//")):
                if drop_comments:
                    comments_dropped += 1
                else:
                    keep.append(body + "\n")
                    comments_kept += 1
                continue
            item = parse_credential_line(body)
            if not item:
                if drop_invalid:
                    invalid_dropped += 1
                else:
                    keep.append(body + "\n")
                continue
            before_parsed += 1
            key = item.email.strip().lower()
            if key in seen:
                duplicate_extra += 1
                continue
            seen.add(key)
            keep.append(
                format_credential_line(
                    item.email,
                    item.password,
                    item.client_id,
                    item.refresh_token,
                )
                + "\n"
            )
            kept_emails.append(item.email)

        unique = len(seen)
        changed = bool(duplicate_extra or invalid_dropped or comments_dropped)
        # also rewrite if any kept line was non-canonical (normalized form always)
        # Compare normalized body to original non-comment credential content size
        if not changed and before_parsed:
            # detect non-canonical original lines (e.g. JSON still on disk)
            orig_cred_bodies = []
            for raw in lines:
                body = raw[:-1] if raw.endswith("\n") else raw
                if body.endswith("\r"):
                    body = body[:-1]
                s = body.strip()
                if not s or s.startswith(("#", "//")):
                    continue
                if parse_credential_line(body):
                    orig_cred_bodies.append(body)
            canon_bodies = [
                ln[:-1] if ln.endswith("\n") else ln
                for ln in keep
                if parse_credential_line(ln)
            ]
            if orig_cred_bodies != canon_bodies:
                changed = True

        backup_path = None
        if not dry_run and changed:
            bak = live.with_name(live.name + f".bak-compact-{stamp}")
            shutil.copy2(live, bak)
            backup_path = str(bak)
            tmp = live.with_name(live.name + f".tmp-compact-{os.getpid()}-{stamp}")
            tmp.write_text("".join(keep), encoding="utf-8")
            os.replace(tmp, live)

        after_unique = unique
        summary_bits = [
            f"唯一 {after_unique}",
            f"去掉重复 {duplicate_extra}",
        ]
        if invalid_dropped:
            summary_bits.append(f"去掉无效 {invalid_dropped}")
        if comments_dropped:
            summary_bits.append(f"去掉注释/空行 {comments_dropped}")
        if dry_run:
            head = "预览精简" if changed else "无需精简"
        else:
            head = "已精简" if changed else "无需精简"
        summary = f"{head} · " + " · ".join(summary_bits)

        return {
            "ok": True,
            "dry_run": bool(dry_run),
            "changed": changed,
            "path": str(live),
            "backup_path": backup_path,
            "before_parsed": before_parsed,
            "unique": after_unique,
            "duplicate_extra": duplicate_extra,
            "invalid_dropped": invalid_dropped,
            "comments_kept": comments_kept,
            "comments_dropped": comments_dropped,
            "after_lines": sum(1 for ln in keep if parse_credential_line(ln)),
            "summary": summary,
            # sample only — emails are not secrets
            "sample_kept": kept_emails[:5],
        }


def pool_stats(path: Path | str, *, dead_path: Path | str | None = None) -> dict[str, Any]:
    """Return pool totals / by_domain counts (no secrets)."""
    live = Path(path)
    accounts = load_pool(live) if live.is_file() else []
    by_domain: dict[str, int] = {}
    for acc in accounts:
        dom = acc.domain
        if not dom:
            dom = "other"
        elif dom not in KNOWN_DOMAINS:
            # keep real domain so UI can show unexpected domains; bucket rare ones
            pass
        by_domain[dom] = by_domain.get(dom, 0) + 1
    dead = Path(dead_path) if dead_path is not None else dead_archive_path(live)
    dead_total = 0
    if dead.is_file():
        try:
            dead_total = sum(
                1
                for ln in dead.read_text(encoding="utf-8-sig").splitlines()
                if ln.strip() and not ln.lstrip().startswith(("#", "//"))
            )
        except OSError:
            dead_total = 0
    scan = scan_pool_file(live) if live.is_file() else scan_pool_file("")
    return {
        "path": str(live),
        "total": len(accounts),
        "unique": len(accounts),
        "raw_lines": int(scan.get("raw_lines") or 0),
        "parsed_lines": int(scan.get("parsed_lines") or 0),
        "duplicate_extra": int(scan.get("duplicate_extra") or 0),
        "invalid_lines": int(scan.get("invalid_lines") or 0),
        "needs_compact": bool(scan.get("needs_compact")),
        "by_domain": dict(sorted(by_domain.items(), key=lambda kv: (-kv[1], kv[0]))),
        "dead_path": str(dead),
        "dead_total": dead_total,
        "known_domains": list(KNOWN_DOMAINS),
        "quarantinable_statuses": sorted(QUARANTINABLE_STATUSES),
    }


def filter_by_domains(
    accounts: Iterable[Credential],
    domains: list[str] | None,
) -> list[Credential]:
    """Filter by email domain (case-insensitive). Empty/None domains = all."""
    if not domains:
        return list(accounts)
    wanted = {d.strip().lower() for d in domains if d and str(d).strip()}
    if not wanted:
        return list(accounts)
    return [a for a in accounts if a.domain in wanted]


def sample_accounts(
    accounts: list[Credential],
    limit: int,
    *,
    seed: int | None = None,
) -> list[Credential]:
    """Sample up to ``limit`` accounts. Deterministic if seed set."""
    if limit <= 0 or not accounts:
        return []
    pool = list(accounts)
    if seed is not None:
        rng = random.Random(int(seed))
        rng.shuffle(pool)
    else:
        random.shuffle(pool)
    return pool[: min(int(limit), len(pool))]


def classify_error(message: str | Exception | None) -> str:
    """Map Microsoft / network error text to a status code.

    Prefer AADSTS / OAuth error codes over broad English substrings.
    """
    if message is None:
        return STATUS_UNKNOWN
    text = str(message).strip()
    if not text:
        return STATUS_UNKNOWN
    low = text.lower()

    # Network / transport first only on strong signals (not bare "connection")
    if any(
        k in low
        for k in (
            "timed out",
            "timeout",
            "connection reset",
            "connection refused",
            "connection aborted",
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
            "failed to establish a new connection",
            "max retries exceeded",
            "network is unreachable",
            "broken pipe",
            "urlopen error",
            "errno 8",
            "errno 51",
            "errno 61",
            "errno 110",
            "errno 111",
        )
    ) or isinstance(message, (TimeoutError, ConnectionError, urlerror.URLError)):
        # URLError wrapping HTTPError is rare; HTTP bodies handled below
        if not isinstance(message, urlerror.HTTPError):
            return STATUS_NETWORK_ERROR

    if "abuse" in low:
        return STATUS_ABUSE_MODE

    # Grant expired (specific AADSTS before generic invalid_grant)
    if any(
        k in low
        for k in (
            "aadsts700082",
            "aadsts70008",
            "grant is expired",
            "grant expired",
            "expired or revoked",
            "lifetime of the token",
            "token has expired due to inactivity",
        )
    ):
        return STATUS_GRANT_EXPIRED

    if any(
        k in low
        for k in (
            "aadsts70000",
            "aadsts9002313",
            "aadsts50173",
            "aadsts50076",
            "aadsts50034",
            "aadsts50055",
            "aadsts50057",
            "invalid_grant",
            "invalid_client",
            "unauthorized_client",
            "interaction_required",
            "refresh token has been revoked",
            "provided grant is invalid",
            "the user account",
        )
    ):
        return STATUS_REFRESH_INVALID

    if any(k in low for k in ("missing fields", "invalid line", "malformed credential")):
        return STATUS_PARSE_ERROR

    # Weak network fallback after IdP codes checked
    if isinstance(message, (TimeoutError, ConnectionError, OSError, urlerror.URLError)):
        return STATUS_NETWORK_ERROR

    return STATUS_UNKNOWN


def _truncate_ms_error(msg: str | None) -> str:
    text = str(msg or "").replace("\n", " ").strip()
    if len(text) > _MS_ERROR_MAX:
        return text[: _MS_ERROR_MAX - 1] + "…"
    return text


def _sanitize_reason(reason: str) -> str:
    """Reason is stored in dead file as a field; strip separators/newlines."""
    tag = (reason or "quarantine").replace("\n", " ").replace("\r", " ").strip()
    tag = tag.replace("----", "-")
    return (tag or "quarantine")[:128]


def _extract_error_text(raw: str, token_data: Any) -> str:
    if isinstance(token_data, dict):
        err = (
            token_data.get("error_description")
            or token_data.get("error")
            or ""
        )
        if err:
            return str(err)
    return (raw or "")[:200]


def _default_refresh(
    account: Credential,
    *,
    timeout: float = _DEFAULT_PER_ACCOUNT_TIMEOUT,
) -> tuple[bool, str, str | None]:
    """Minimal OAuth refresh against Microsoft endpoints (stdlib only).

    Returns (ok, error_message, new_refresh_token_or_None).
    Stops early on terminal IdP errors (no need to burn all 4 endpoints).
    """
    last_error = ""
    per_ep_timeout = max(3.0, min(float(timeout), 30.0))
    for url, extra in TOKEN_ENDPOINTS:
        try:
            form = {
                "client_id": account.client_id,
                "refresh_token": account.refresh_token,
                "grant_type": "refresh_token",
                **extra,
            }
            body = urlparse.urlencode(form).encode("utf-8")
            req = urlrequest.Request(
                url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
            with urlrequest.urlopen(req, timeout=per_ep_timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            try:
                token_data = json.loads(raw) if raw else {}
            except Exception:
                token_data = {}
            if isinstance(token_data, dict) and token_data.get("access_token"):
                new_rt = token_data.get("refresh_token")
                if new_rt and str(new_rt) != account.refresh_token:
                    return True, "", str(new_rt)
                return True, "", None
            last_error = _extract_error_text(raw, token_data) or "empty token response"
            if _TERMINAL_ERROR_RE.search(last_error):
                break
        except urlerror.HTTPError as exc:
            try:
                body_txt = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body_txt = str(exc)
            try:
                token_data = json.loads(body_txt) if body_txt else {}
            except Exception:
                token_data = {}
            last_error = _extract_error_text(body_txt, token_data) or str(exc)
            if _TERMINAL_ERROR_RE.search(last_error):
                break
            continue
        except (urlerror.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
            continue
        except Exception as exc:  # noqa: BLE001 — classify later
            last_error = str(exc)
            continue
    return False, last_error or "refresh failed", None


def update_refresh_token_in_pool(
    live_path: Path | str,
    email: str,
    new_refresh_token: str,
) -> bool:
    """Rewrite one account's refresh_token in the live pool (rotation writeback).

    Holds the process file lock. Returns True if a line was updated.
    """
    if not email or not new_refresh_token:
        return False
    live = Path(live_path)
    key = email.strip().lower()
    with _file_lock:
        if not live.is_file():
            return False
        text = live.read_text(encoding="utf-8-sig")
        lines = text.splitlines(keepends=True)
        changed = False
        out: list[str] = []
        for raw in lines:
            item = parse_credential_line(raw)
            if item and item.email.strip().lower() == key:
                out.append(
                    f"{item.email}----{item.password}----"
                    f"{item.client_id}----{new_refresh_token}\n"
                )
                changed = True
            else:
                if raw.endswith("\n") or raw == "":
                    out.append(raw)
                else:
                    out.append(raw + "\n")
        if not changed:
            return False
        tmp = live.with_name(live.name + f".tmp-rt-{os.getpid()}")
        tmp.write_text("".join(out), encoding="utf-8")
        os.replace(tmp, live)
        return True


def probe_one(
    account: Credential,
    *,
    refresh_fn: RefreshFn | None = None,
    live_path: Path | str | None = None,
    writeback_rotated: bool = True,
) -> ProbeResult:
    """Probe a single credential. Returns public ProbeResult (no secrets)."""
    domain = account.domain
    email = account.email
    if not account.client_id or not account.refresh_token or "@" not in email:
        return ProbeResult(
            email=email,
            domain=domain,
            status=STATUS_PARSE_ERROR,
            reason=STATUS_PARSE_ERROR,
            ms_error="missing fields",
            quarantinable=False,
        )
    fn = refresh_fn or (lambda acc: _default_refresh(acc))
    try:
        result = fn(account)
        # Support both (ok, err) and (ok, err, new_rt) callables
        if len(result) == 2:
            ok, err = result  # type: ignore[misc]
            new_rt = None
        else:
            ok, err, new_rt = result  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        status = classify_error(exc)
        return ProbeResult(
            email=email,
            domain=domain,
            status=status,
            reason=status,
            ms_error=_truncate_ms_error(exc),
            quarantinable=is_quarantinable(status),
        )
    if ok:
        if writeback_rotated and new_rt and live_path is not None:
            try:
                update_refresh_token_in_pool(live_path, email, str(new_rt))
            except OSError:
                # Probe still succeeded; rotation writeback best-effort
                pass
            else:
                account.refresh_token = str(new_rt)
        return ProbeResult(
            email=email,
            domain=domain,
            status=STATUS_OK,
            reason="",
            ms_error="",
            quarantinable=False,
        )
    status = classify_error(err)
    return ProbeResult(
        email=email,
        domain=domain,
        status=status,
        reason=status,
        ms_error=_truncate_ms_error(err),
        quarantinable=is_quarantinable(status),
    )


def probe_sample(
    path: Path | str,
    *,
    domains: list[str] | None = None,
    limit: int = 30,
    seed: int | None = None,
    concurrency: int = 4,
    refresh_fn: RefreshFn | None = None,
    writeback_rotated: bool = True,
    wall_seconds: float = _DEFAULT_WALL_SECONDS,
) -> dict[str, Any]:
    """Sample + concurrent OAuth probe. Never returns secrets.

    - concurrency hard-capped at 8
    - limit hard-capped at 200
    - wall_seconds caps total wait (remaining futures cancelled; partial results kept)
    - process-global lock: only one probe wave at a time in this process
    """
    limit = max(1, min(int(limit), 200))
    concurrency = max(1, min(int(concurrency), 8))
    wall = max(5.0, float(wall_seconds or _DEFAULT_WALL_SECONDS))
    live = Path(path)
    accounts = load_pool(live)
    filtered = filter_by_domains(accounts, domains)
    sample = sample_accounts(filtered, limit, seed=seed)

    results: list[ProbeResult | None] = [None] * len(sample)
    timed_out = False
    if sample:
        if not _probe_global_lock.acquire(blocking=False):
            raise RuntimeError("another mail probe is already running in this process")
        try:
            deadline = time.monotonic() + wall
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {
                    pool.submit(
                        probe_one,
                        acc,
                        refresh_fn=refresh_fn,
                        live_path=live if writeback_rotated else None,
                        writeback_rotated=writeback_rotated,
                    ): idx
                    for idx, acc in enumerate(sample)
                }
                pending = set(futures.keys())
                while pending:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        for fut in pending:
                            fut.cancel()
                        # mark unfinished as network_error (inconclusive)
                        for fut in pending:
                            idx = futures[fut]
                            if results[idx] is None:
                                acc = sample[idx]
                                results[idx] = ProbeResult(
                                    email=acc.email,
                                    domain=acc.domain,
                                    status=STATUS_NETWORK_ERROR,
                                    reason=STATUS_NETWORK_ERROR,
                                    ms_error="probe wall-clock timeout",
                                    quarantinable=False,
                                )
                        break
                    done, pending = wait_done(pending, timeout=min(1.0, remaining))
                    for fut in done:
                        idx = futures[fut]
                        try:
                            results[idx] = fut.result()
                        except Exception as exc:  # noqa: BLE001
                            acc = sample[idx]
                            status = classify_error(exc)
                            results[idx] = ProbeResult(
                                email=acc.email,
                                domain=acc.domain,
                                status=status,
                                reason=status,
                                ms_error=_truncate_ms_error(exc),
                                quarantinable=is_quarantinable(status),
                            )
        finally:
            _probe_global_lock.release()

    final: list[ProbeResult] = [r for r in results if r is not None]
    by_status: dict[str, int] = {}
    ok_n = 0
    quarantinable_n = 0
    for r in final:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        if r.status == STATUS_OK:
            ok_n += 1
        if r.quarantinable:
            quarantinable_n += 1
    dead_n = len(final) - ok_n
    return {
        "probed": len(final),
        "ok": ok_n,
        "dead": dead_n,
        "quarantinable": quarantinable_n,
        "by_status": by_status,
        "timed_out": timed_out,
        "results": [r.to_public_dict() for r in final],
    }


def wait_done(futures: set, timeout: float):
    """Wait for some futures to complete (stdlib helper)."""
    from concurrent.futures import wait, FIRST_COMPLETED

    if not futures:
        return set(), set()
    done, not_done = wait(futures, timeout=timeout, return_when=FIRST_COMPLETED)
    return done, not_done


def quarantine(
    live_path: Path | str,
    emails: list[str],
    *,
    reason: str = "quarantine",
    dead_path: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Remove explicit emails from live pool → dead archive. Backup first.

    ``emails`` must be a non-empty explicit list (no implicit "all failures").
    Atomic-ish order: backup → write dead temp+append → rewrite live via temp+replace.
    """
    if not emails:
        raise ValueError("emails must be a non-empty list")
    wanted = {e.strip().lower() for e in emails if e and str(e).strip()}
    if not wanted:
        raise ValueError("emails must be a non-empty list")

    live = Path(live_path)
    if not live.is_file():
        raise FileNotFoundError(f"mail credentials file not found: {live}")
    dead = Path(dead_path) if dead_path is not None else dead_archive_path(live)
    reason_tag = _sanitize_reason(reason)
    ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp = time.strftime("%Y%m%d_%H%M%S")

    with _file_lock:
        text = live.read_text(encoding="utf-8-sig")
        lines = text.splitlines(keepends=True)
        keep: list[str] = []
        dead_lines: list[str] = []
        found: set[str] = set()
        for raw in lines:
            item = parse_credential_line(raw)
            if not item:
                keep.append(raw if raw.endswith("\n") or raw == "" else raw + "\n")
                continue
            key = item.email.strip().lower()
            if key in wanted:
                found.add(key)
                base = (
                    f"{item.email}----{item.password}----"
                    f"{item.client_id}----{item.refresh_token}"
                )
                dead_lines.append(f"{base}----{reason_tag}----{ts}\n")
            else:
                keep.append(raw if raw.endswith("\n") or raw == "" else raw + "\n")

        bak = live.with_name(live.name + f".bak-probe-{stamp}")
        shutil.copy2(live, bak)

        # 1) Append dead archive first so credentials are not lost if live rewrite fails
        dead.parent.mkdir(parents=True, exist_ok=True)
        if dead_lines:
            with dead.open("a", encoding="utf-8") as f:
                f.writelines(dead_lines)
                f.flush()
                os.fsync(f.fileno())

        # 2) Rewrite live via temp + atomic replace
        tmp = live.with_name(live.name + f".tmp-probe-{os.getpid()}-{stamp}")
        tmp.write_text("".join(keep), encoding="utf-8")
        os.replace(tmp, live)

        live_total_after = sum(1 for raw in keep if parse_credential_line(raw))

    return {
        "removed": len(dead_lines),
        "not_found": len(wanted - found),
        "dead_path": str(dead),
        "backup_path": str(bak),
        "live_total_after": live_total_after,
    }


def public_probe_result(row: dict[str, Any] | ProbeResult) -> dict[str, Any]:
    """Ensure a probe row is public (no secrets)."""
    if isinstance(row, ProbeResult):
        return row.to_public_dict()
    status = str(row.get("status") or STATUS_UNKNOWN)
    return {
        "email": str(row.get("email") or ""),
        "domain": str(row.get("domain") or ""),
        "status": status,
        "reason": str(row.get("reason") or ""),
        "ms_error": str(row.get("ms_error") or "")[:_MS_ERROR_MAX],
        "quarantinable": bool(
            row["quarantinable"]
            if "quarantinable" in row
            else is_quarantinable(status)
        ),
    }
