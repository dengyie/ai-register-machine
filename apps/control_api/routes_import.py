"""Import multipart routes."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile

from apps.control_api.imports_ops import (
    import_auths,
    import_mail,
    import_nodes,
    import_pack,
    save_upload,
    staging_dir,
)
from apps.control_api.schemas import ImportResultOut
from apps.control_api.settings import get_settings

router = APIRouter(tags=["import"])


@router.post("/api/import/nodes", response_model=ImportResultOut)
async def post_import_nodes(
    file: UploadFile = File(...),
    dry_run: bool = Form(default=False),
    replace: bool = Form(default=False),
) -> ImportResultOut:
    s = get_settings()
    data = await file.read()
    path = save_upload(s.project_root, file.filename or "nodes.yaml", data, s.max_upload_bytes)
    result = import_nodes(s.project_root, path, dry_run=dry_run, replace=replace)
    return ImportResultOut(ok=bool(result.get("ok")), detail="nodes import", result=result)


@router.post("/api/import/mail", response_model=ImportResultOut)
async def post_import_mail(
    content: str = Form(...),
    mode: str = Form(default="append"),
) -> ImportResultOut:
    if mode not in {"append", "replace"}:
        raise ValueError("mode must be append or replace")
    s = get_settings()
    result = import_mail(s.project_root, content, mode=mode)  # type: ignore[arg-type]
    return ImportResultOut(ok=True, detail="mail import", result=result)


@router.post("/api/import/auths", response_model=ImportResultOut)
async def post_import_auths(
    file: UploadFile = File(...),
    no_remote: bool = Form(default=True),
) -> ImportResultOut:
    """Accept a zip of xai-*.json or a single json; extract to staging dir."""
    s = get_settings()
    data = await file.read()
    path = save_upload(s.project_root, file.filename or "auths.zip", data, s.max_upload_bytes)
    src_dir: Path
    if path.suffix.lower() == ".zip" or zipfile.is_zipfile(path):
        extract = staging_dir(s.project_root) / f"auths_{path.stem}"
        if extract.exists():
            shutil.rmtree(extract)
        extract.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "r") as zf:
            for info in zf.infolist():
                if info.filename.startswith("/") or ".." in Path(info.filename).parts:
                    raise ValueError(f"unsafe zip entry: {info.filename}")
            zf.extractall(extract)
        src_dir = extract
    else:
        # single file → put in a dir
        src_dir = staging_dir(s.project_root) / f"auths_one_{path.stem}"
        src_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, src_dir / path.name)
    result = import_auths(s.project_root, src_dir, no_remote=no_remote)
    return ImportResultOut(ok=bool(result.get("ok")), detail="auths import", result=result)


@router.post("/api/import/pack", response_model=ImportResultOut)
async def post_import_pack(
    file: UploadFile = File(...),
    apply: bool = Form(default=False),
) -> ImportResultOut:
    s = get_settings()
    data = await file.read()
    path = save_upload(s.project_root, file.filename or "pack.zip", data, s.max_upload_bytes)
    result = import_pack(s.project_root, path, apply=apply)
    return ImportResultOut(ok=True, detail="pack import", result=result)
