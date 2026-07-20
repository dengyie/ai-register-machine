"""Import helpers: nodes, mail, auths, config packs."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Literal

from apps.control_api.config_io import load_config


def staging_dir(root: Path) -> Path:
    d = root / "output" / "web_uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_under(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    root_r = root.resolve()
    staging = staging_dir(root).resolve()
    if resolved == root_r or root_r in resolved.parents:
        return resolved
    if resolved == staging or staging in resolved.parents:
        return resolved
    raise ValueError(f"path escapes project root: {path}")


def save_upload(root: Path, filename: str, data: bytes, max_bytes: int) -> Path:
    if len(data) > max_bytes:
        raise ValueError(f"upload exceeds max_upload_bytes={max_bytes}")
    safe = Path(filename).name
    if not safe or safe in {".", ".."}:
        raise ValueError("invalid filename")
    dest = staging_dir(root) / f"{int(time.time())}_{safe}"
    dest.write_bytes(data)
    return ensure_under(root, dest)


def import_nodes(
    root: Path,
    file_path: Path,
    *,
    dry_run: bool = False,
    replace: bool = False,
) -> dict[str, Any]:
    path = ensure_under(root, file_path)
    script = root / "scripts" / "import_nodes.py"
    if not script.is_file():
        raise FileNotFoundError(str(script))
    argv = [sys.executable, str(script), str(path)]
    if dry_run:
        argv.append("--dry-run")
    if replace:
        argv.append("--replace")
    proc = subprocess.run(
        argv,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {
        "exit": proc.returncode,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-2000:],
        "path": str(path),
        "dry_run": dry_run,
        "replace": replace,
        "ok": proc.returncode == 0,
    }


def import_mail(
    root: Path,
    content: str,
    *,
    mode: Literal["append", "replace"] = "append",
) -> dict[str, Any]:
    cfg = load_config(root)
    rel = str(cfg.get("hotmail_accounts_file") or "mail_credentials.txt")
    target = ensure_under(root, (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel))
    # Force under root even if absolute path outside
    if root.resolve() not in target.parents and target != root.resolve():
        target = root / "mail_credentials.txt"
    backup = None
    if target.is_file():
        bak = target.with_name(target.name + f".bak-web-{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(target, bak)
        backup = str(bak)
    text = content if content.endswith("\n") or content == "" else content + "\n"
    if mode == "replace" or not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        lines = len([ln for ln in text.splitlines() if ln.strip()])
    else:
        with target.open("a", encoding="utf-8") as f:
            f.write(text)
        lines = len([ln for ln in text.splitlines() if ln.strip()])
    return {"ok": True, "path": str(target), "backup": backup, "mode": mode, "lines_written": lines}


def import_auths(
    root: Path,
    src_dir: Path,
    *,
    no_remote: bool = True,
) -> dict[str, Any]:
    src = ensure_under(root, src_dir)
    if not src.is_dir():
        raise ValueError(f"auth src is not a directory: {src}")
    script = root / "scripts" / "import_cpa_auth_dir.py"
    if not script.is_file():
        raise FileNotFoundError(str(script))
    argv = [sys.executable, str(script), "--src", str(src)]
    if no_remote:
        argv.append("--no-remote")
    proc = subprocess.run(
        argv,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    return {
        "exit": proc.returncode,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-2000:],
        "src": str(src),
        "no_remote": no_remote,
        "ok": proc.returncode == 0,
    }


def import_pack(root: Path, zip_path: Path, *, apply: bool = False) -> dict[str, Any]:
    zpath = ensure_under(root, zip_path)
    extract_to = staging_dir(root) / f"pack_{int(time.time())}"
    extract_to.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath, "r") as zf:
        for info in zf.infolist():
            name = info.filename
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"unsafe zip entry: {name}")
        zf.extractall(extract_to)

    found = {
        "config": next(extract_to.rglob("config.json"), None),
        "nodes": next(extract_to.rglob("nodes.json"), None),
        "mail": next(
            (
                p
                for p in extract_to.rglob("*")
                if p.is_file() and p.name in {"mail_credentials.txt", "mail_credentials.example.txt"}
            ),
            None,
        ),
    }
    plan = {k: str(v) if v else None for k, v in found.items()}
    applied: dict[str, str] = {}
    if apply:
        if found["config"]:
            dest = root / "config.json"
            if dest.is_file():
                shutil.copy2(dest, dest.with_name(f"config.json.bak-web-pack-{time.strftime('%Y%m%d_%H%M%S')}"))
            shutil.copy2(found["config"], dest)
            applied["config"] = str(dest)
        if found["nodes"]:
            dest = root / "nodes.json"
            if dest.is_file():
                shutil.copy2(dest, dest.with_name(f"nodes.json.bak-web-pack-{time.strftime('%Y%m%d_%H%M%S')}"))
            shutil.copy2(found["nodes"], dest)
            applied["nodes"] = str(dest)
        if found["mail"]:
            dest = root / "mail_credentials.txt"
            if dest.is_file():
                shutil.copy2(dest, dest.with_name(f"mail_credentials.txt.bak-web-pack-{time.strftime('%Y%m%d_%H%M%S')}"))
            shutil.copy2(found["mail"], dest)
            applied["mail"] = str(dest)
    return {
        "ok": True,
        "extract_to": str(extract_to),
        "plan": plan,
        "applied": applied,
        "apply": apply,
    }
