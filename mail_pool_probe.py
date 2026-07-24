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


def parse_credential_line(line: str, line_no: int = 0) -> Credential | None:
    """Parse ``email----password----client_id----refresh_token`` (first 4 fields)."""
    raw = line.rstrip("\n")
    if not raw.strip() or raw.lstrip().startswith(("#", "//")):
        return None
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
    return {
        "path": str(live),
        "total": len(accounts),
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
