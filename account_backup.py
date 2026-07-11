"""Local project backup for registered accounts + CPA auth files.

Secrets stay under backups/ (gitignored). Safe to call after each success.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
_BACKUP_ROOT = _ROOT / "backups"


def _project_root(root: str | Path | None = None) -> Path:
    return Path(root).expanduser().resolve() if root else _ROOT


def _safe_copy(src: Path, dst: Path) -> bool:
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    try:
        os.chmod(dst, 0o600)
    except OSError:
        pass
    return True


def _parse_accounts(accounts_file: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not accounts_file.is_file():
        return rows
    for line in accounts_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----")
        email = (parts[0] or "").strip()
        if not email:
            continue
        rows.append(
            {
                "email": email,
                "has_password": len(parts) > 1 and bool(parts[1].strip()),
                "has_sso": len(parts) > 2 and len(parts[2].strip()) > 20,
            }
        )
    return rows


def snapshot_registered_accounts(
    root: str | Path | None = None,
    *,
    reason: str = "",
    email: str | None = None,
    make_timestamped: bool = True,
    log_callback: Any = None,
) -> dict[str, Any]:
    """Copy accounts ledger + CPA auths into backups/.

    Always refreshes backups/latest.
    Optionally also writes backups/register_YYYYMMDD_HHMMSS.
    """
    log = log_callback or (lambda _m: None)
    proj = _project_root(root)
    backup_root = proj / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    targets: list[Path] = []
    latest = backup_root / "latest"
    targets.append(latest)
    stamped: Path | None = None
    if make_timestamped:
        stamped = backup_root / f"register_{ts}"
        targets.append(stamped)

    sources = {
        "accounts_cli.txt": proj / "accounts_cli.txt",
        "emails_used.txt": proj / "emails_used.txt",
        "emails_error.txt": proj / "emails_error.txt",
    }
    # also pick any accounts_*.txt snapshots from GUI runs
    for p in sorted(proj.glob("accounts_*.txt")):
        sources[p.name] = p

    cpa_src = proj / "cpa_auths"
    cpa_files = sorted(cpa_src.glob("xai-*.json")) if cpa_src.is_dir() else []

    written: list[str] = []
    for target in targets:
        if target.exists():
            if target.is_dir():
                # refresh in place for latest; recreate for stamped
                if target.name == "latest":
                    pass
                else:
                    shutil.rmtree(target)
                    target.mkdir(parents=True, exist_ok=True)
            else:
                target.unlink()
                target.mkdir(parents=True, exist_ok=True)
        else:
            target.mkdir(parents=True, exist_ok=True)

        for name, src in sources.items():
            if _safe_copy(src, target / name):
                written.append(f"{target.name}/{name}")

        cpa_dst = target / "cpa_auths"
        cpa_dst.mkdir(exist_ok=True)
        # prune stale in latest to mirror current set
        if target.name == "latest":
            keep = {p.name for p in cpa_files}
            for old in cpa_dst.glob("xai-*.json"):
                if old.name not in keep:
                    try:
                        old.unlink()
                    except OSError:
                        pass
        for p in cpa_files:
            if _safe_copy(p, cpa_dst / p.name):
                written.append(f"{target.name}/cpa_auths/{p.name}")

        accounts = _parse_accounts(proj / "accounts_cli.txt")
        manifest = {
            "created_at": ts,
            "reason": reason or ("post_register" if email else "manual"),
            "trigger_email": email or "",
            "account_count": len(accounts),
            "accounts": accounts,
            "cpa_auth_files": [p.name for p in cpa_files],
            "paths": {
                "backup_dir": str(target),
                "accounts_cli": "accounts_cli.txt",
                "cpa_auths": "cpa_auths/",
            },
            "note": "Contains credentials; keep out of git (backups/ is gitignored).",
        }
        man_path = target / "manifest.json"
        man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(man_path, 0o600)
        except OSError:
            pass

    result = {
        "ok": True,
        "timestamp": ts,
        "latest": str(latest),
        "stamped": str(stamped) if stamped else "",
        "account_count": len(_parse_accounts(proj / "accounts_cli.txt")),
        "cpa_count": len(cpa_files),
        "trigger_email": email or "",
        "reason": reason or "",
        "files": written[:20],
    }
    log(
        f"[backup] accounts={result['account_count']} cpa={result['cpa_count']} "
        f"-> {latest.name}"
        + (f" + {stamped.name}" if stamped else "")
        + (f" ({email})" if email else "")
    )
    return result


def backup_after_success(
    email: str,
    *,
    root: str | Path | None = None,
    cpa_path: str | Path | None = None,
    log_callback: Any = None,
) -> dict[str, Any]:
    """Lightweight post-success backup: refresh latest only (no new timestamp dir).

    Prefer this on every register/mint success to avoid flooding backups/.
    Full timestamped snapshot still available via snapshot_registered_accounts().
    """
    log = log_callback or (lambda _m: None)
    proj = _project_root(root)
    # ensure specific cpa file exists if path given
    if cpa_path:
        src = Path(cpa_path)
        if src.is_file():
            # already under cpa_auths normally; snapshot will pick it up
            pass
    return snapshot_registered_accounts(
        proj,
        reason="success",
        email=email,
        make_timestamped=False,
        log_callback=log,
    )
