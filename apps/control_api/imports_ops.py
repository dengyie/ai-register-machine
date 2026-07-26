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


def _mail_pool_email_set(path: Path) -> set[str]:
    """Lowercased emails currently in the pool file (deduped). No secrets returned."""
    from mail_pool_probe import parse_credential_line

    out: set[str] = set()
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return out
    for raw in text.splitlines():
        item = parse_credential_line(raw)
        if not item:
            continue
        out.add(item.email.strip().lower())
    return out


def _summarize_mail_import(
    *,
    mode: str,
    parsed: int,
    new: int,
    duplicate: int,
    skipped: int,
    pool_before: int,
    pool_after: int,
    sample_new: list[str],
    formats: dict[str, int],
) -> str:
    """Human-readable Chinese summary for UI toast/banner (no secrets)."""
    bits: list[str] = []
    if mode == "replace":
        bits.append(f"替换写入 {new} 条")
    else:
        bits.append(f"新增 {new}")
        if duplicate:
            bits.append(f"重复跳过 {duplicate}")
    if skipped:
        bits.append(f"格式无效 {skipped}")
    fmt_bits = []
    if formats.get("json_objects"):
        fmt_bits.append(f"JSON {formats['json_objects']}")
    if formats.get("csv_rows"):
        fmt_bits.append(f"CSV {formats['csv_rows']}")
    if fmt_bits:
        bits.append("来源 " + "+".join(fmt_bits))
    bits.append(f"池 {pool_before}→{pool_after}")
    if sample_new:
        show = "、".join(sample_new[:3])
        if len(sample_new) > 3:
            show += f" 等{len(sample_new)}个"
        bits.append(f"新号 {show}")
    if new == 0 and duplicate > 0 and skipped == 0:
        return "未新增（全部已在池中）· " + " · ".join(bits)
    if new == 0 and skipped > 0 and duplicate == 0:
        return "未导入（格式无效）· " + " · ".join(bits)
    if new == 0 and parsed == 0:
        return "未导入（无有效凭证）· " + " · ".join(bits)
    if new > 0 and (duplicate or skipped):
        return "部分成功 · " + " · ".join(bits)
    if new > 0:
        return "导入成功 · " + " · ".join(bits)
    return "导入完成 · " + " · ".join(bits)


def import_mail(
    root: Path,
    content: str,
    *,
    mode: Literal["append", "replace"] = "append",
) -> dict[str, Any]:
    """Import mail credentials; normalize vendor formats → dash form before write.

    Accepts classic ``email----password----clientId----refreshToken`` lines,
    vendor JSON (objects/arrays/wrappers), CSV headers, and ``|``/``;``/tab/``:``
    4-field lines. Invalid lines are skipped (not written raw).

    Append mode skips emails already present in the pool (dedupe) and reports
    new vs duplicate counts so the UI can show honest feedback.
    """
    from mail_pool_probe import normalize_credential_text, parse_credential_line

    cfg = load_config(root)
    rel = str(cfg.get("hotmail_accounts_file") or "mail_credentials.txt")
    target = ensure_under(root, (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel))
    # Force under root even if absolute path outside
    if root.resolve() not in target.parents and target != root.resolve():
        target = root / "mail_credentials.txt"

    text, norm_stats = normalize_credential_text(content)
    parsed_lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(("#", "//"))]

    existing = _mail_pool_email_set(target) if target.is_file() else set()
    pool_before = len(existing)

    to_write: list[str] = []
    new_emails: list[str] = []
    dup_emails: list[str] = []
    seen_batch: set[str] = set()
    for ln in parsed_lines:
        item = parse_credential_line(ln)
        if not item:
            continue
        key = item.email.strip().lower()
        if key in seen_batch:
            dup_emails.append(item.email)
            continue
        seen_batch.add(key)
        if mode == "append" and key in existing:
            dup_emails.append(item.email)
            continue
        to_write.append(ln)
        new_emails.append(item.email)

    write_body = ""
    if to_write:
        write_body = "\n".join(to_write)
        if not write_body.endswith("\n"):
            write_body += "\n"

    # replace + zero valid credentials would truncate the pool to empty. A .bak is
    # taken below, but the UI only reports "未导入（格式无效）" — the user would not
    # know the pool was wiped. Refuse instead; append never truncates so it is exempt.
    if mode == "replace" and not write_body and target.is_file():
        raise ValueError(
            f"replace 模式解析出 0 条有效凭证，将清空邮箱池（当前 {pool_before} 个）——已拒绝。"
            f"请检查格式（四段 ---- / JSON / CSV / 管道分隔），或改用 append。"
        )

    backup = None
    if target.is_file() and (mode == "replace" or write_body):
        bak = target.with_name(target.name + f".bak-web-{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(target, bak)
        backup = str(bak)

    if mode == "replace" or not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(write_body, encoding="utf-8")
    elif write_body:
        with target.open("a", encoding="utf-8") as f:
            f.write(write_body)

    pool_after = len(_mail_pool_email_set(target)) if target.is_file() else 0
    lines_written = len(to_write)
    skipped = int(norm_stats.get("skipped") or 0)
    # batch-internal dups counted in duplicate
    duplicate = len(dup_emails)
    formats = {
        "json_objects": int(norm_stats.get("json_objects") or 0),
        "csv_rows": int(norm_stats.get("csv_rows") or 0),
    }
    summary = _summarize_mail_import(
        mode=mode,
        parsed=len(parsed_lines),
        new=lines_written,
        duplicate=duplicate,
        skipped=skipped,
        pool_before=pool_before,
        pool_after=pool_after,
        sample_new=new_emails,
        formats=formats,
    )
    # status for UI: success | partial | empty
    if lines_written > 0 and (duplicate or skipped):
        status = "partial"
    elif lines_written > 0:
        status = "success"
    else:
        status = "empty"

    return {
        "ok": True,
        "status": status,
        "summary": summary,
        "path": str(target),
        "backup": backup,
        "mode": mode,
        "lines_written": lines_written,
        "parsed": len(parsed_lines),
        "new": lines_written,
        "duplicate": duplicate,
        "skipped": skipped,
        "pool_before": pool_before,
        "pool_after": pool_after,
        "new_emails": new_emails[:20],
        "duplicate_emails": dup_emails[:20],
        "normalized": norm_stats,
        "formats": formats,
    }


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
