"""Translation task API routes."""

import json
import os
import shutil
import uuid
import zipfile
from io import BytesIO
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from ..config import (
    MAX_FILE_SIZE,
    UPLOAD_DIR,
    RESULT_DIR,
    TASK_EXPIRE_DAYS,
    TOKEN_EXPIRE_DAYS,
    MAX_PARALLEL_TASKS,
)
from ..database import get_db
from ..services.docx_handler import extract_paragraphs, insert_translations, validate_docx
from ..services.excel_handler import extract_cells, insert_cell_translations, validate_xlsx
from ..services.translator import translate_all

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

SUPPORTED_FILE_EXTENSIONS = {".docx", ".xlsx"}
RESULT_MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class BatchDownloadRequest(BaseModel):
    task_ids: list[str]


def _get_token(request: Request) -> str:
    # Middleware always sets this
    return getattr(request.state, "token", "") or request.cookies.get("token", "")


def _file_extension(filename: str | None) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _download_filename(original_filename: str | None) -> str:
    base_name = original_filename or "document"
    ext = _file_extension(base_name)
    if ext in SUPPORTED_FILE_EXTENSIONS:
        base_name = base_name[:-len(ext)]
    else:
        ext = ".docx"
    return f"{base_name}_双语{ext}"


def _safe_zip_name(filename: str) -> str:
    return filename.replace("\\", "_").replace("/", "_").strip() or "document_双语.docx"


def _unique_zip_name(filename: str, used_names: set[str]) -> str:
    filename = _safe_zip_name(filename)
    if filename not in used_names:
        used_names.add(filename)
        return filename

    stem, ext = os.path.splitext(filename)
    index = 2
    while True:
        candidate = f"{stem}_{index}{ext}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        index += 1


def _result_media_type(filename: str | None) -> str:
    return RESULT_MEDIA_TYPES.get(_file_extension(filename), RESULT_MEDIA_TYPES[".docx"])


@router.post("")
async def create_task(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_lang: str = Form("zh"),
    target_lang: str = Form("en"),
    glossary_id: Optional[str] = Form(None),
    use_builtin_glossary: str = Form("false"),
):
    """Upload DOCX/XLSX file and create translation task."""
    # Token from middleware
    token = _get_token(request)
    if not token:
        raise HTTPException(401, "Token missing")

    if source_lang == target_lang:
        raise HTTPException(400, "源语言和目标语言不能相同")

    # Validate file
    ext = _file_extension(file.filename)
    if ext not in SUPPORTED_FILE_EXTENSIONS:
        raise HTTPException(400, "仅支持 .docx / .xlsx 文件")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"文件超过 {MAX_FILE_SIZE // 1024 // 1024}MB 限制")

    # Check parallel task limit
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM translation_tasks WHERE token = ? AND status NOT IN ('completed', 'failed')",
        (token,),
    )
    row = await cursor.fetchone()
    if row["cnt"] >= MAX_PARALLEL_TASKS:
        await db.close()
        raise HTTPException(429, f"最多 {MAX_PARALLEL_TASKS} 个并行任务")

    # Create task
    task_id = str(uuid.uuid4())
    task_dir = os.path.join(UPLOAD_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    original_path = os.path.join(task_dir, f"original{ext}")
    with open(original_path, "wb") as f:
        f.write(content)

    expires_at = (datetime.now(timezone.utc) + timedelta(days=TASK_EXPIRE_DAYS)).isoformat()

    await db.execute(
        """INSERT INTO translation_tasks
        (id, token, original_filename, original_path, glossary_id, source_lang, target_lang, status, expires_at, use_builtin_glossary)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (task_id, token, file.filename, original_path, glossary_id, source_lang, target_lang, expires_at,
         1 if use_builtin_glossary == "true" else 0),
    )
    await db.commit()
    await db.close()

    background_tasks.add_task(run_translation, task_id)

    return {"task_id": task_id, "status": "pending"}


@router.get("")
async def list_tasks(request: Request, page: int = 1, page_size: int = 20):
    """List tasks for current token."""
    token = _get_token(request)
    if not token:
        return {"total": 0, "items": []}

    db = await get_db()
    offset = (page - 1) * page_size

    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM translation_tasks WHERE token = ?",
        (token,),
    )
    total = (await cursor.fetchone())["cnt"]

    cursor = await db.execute(
        """SELECT id, original_filename, source_lang, target_lang, status, progress,
                  total_paragraphs, translated_paragraphs, created_at, completed_at
           FROM translation_tasks WHERE token = ?
           ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        (token, page_size, offset),
    )
    rows = await cursor.fetchall()
    await db.close()

    items = [{
        "task_id": r["id"],
        "original_filename": r["original_filename"],
        "source_lang": r["source_lang"],
        "target_lang": r["target_lang"],
        "status": r["status"],
        "progress": r["progress"],
        "total_paragraphs": r["total_paragraphs"],
        "translated_paragraphs": r["translated_paragraphs"],
        "created_at": r["created_at"],
        "completed_at": r["completed_at"],
    } for r in rows]

    return {"total": total, "items": items}


@router.post("/batch-download")
async def batch_download_tasks(payload: BatchDownloadRequest, request: Request):
    """Download multiple completed translated DOCX files as a ZIP archive."""
    token = _get_token(request)
    task_ids = list(dict.fromkeys(payload.task_ids))
    if not task_ids:
        raise HTTPException(400, "请选择要下载的任务")

    placeholders = ",".join("?" for _ in task_ids)
    db = await get_db()
    cursor = await db.execute(
        f"""SELECT id, result_path, status, original_filename
            FROM translation_tasks
            WHERE token = ? AND id IN ({placeholders})""",
        (token, *task_ids),
    )
    rows = await cursor.fetchall()
    await db.close()

    rows_by_id = {row["id"]: row for row in rows}
    missing_ids = [task_id for task_id in task_ids if task_id not in rows_by_id]
    if missing_ids:
        raise HTTPException(404, "部分任务不存在或无权下载")

    invalid_rows = [
        row for row in rows
        if row["status"] != "completed" or not row["result_path"] or not os.path.exists(row["result_path"])
    ]
    if invalid_rows:
        raise HTTPException(400, "只能批量下载已完成且未过期的任务")

    buffer = BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for task_id in task_ids:
            row = rows_by_id[task_id]
            archive.write(
                row["result_path"],
                arcname=_unique_zip_name(_download_filename(row["original_filename"]), used_names),
            )

    buffer.seek(0)
    headers = {"Content-Disposition": "attachment; filename=translated-documents.zip"}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request):
    """Get task detail (for polling progress)."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, status, progress, total_paragraphs, translated_paragraphs, error_message FROM translation_tasks WHERE id = ?",
        (task_id,),
    )
    row = await cursor.fetchone()
    await db.close()

    if not row:
        raise HTTPException(404, "任务不存在")

    return {
        "task_id": row["id"],
        "status": row["status"],
        "progress": row["progress"],
        "total_paragraphs": row["total_paragraphs"],
        "translated_paragraphs": row["translated_paragraphs"],
        "error_message": row["error_message"],
    }


@router.get("/{task_id}/download")
async def download_task(task_id: str, request: Request):
    """Download translated document."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT result_path, status, original_filename FROM translation_tasks WHERE id = ?",
        (task_id,),
    )
    row = await cursor.fetchone()
    await db.close()

    if not row:
        raise HTTPException(404, "任务不存在")
    if row["status"] != "completed":
        raise HTTPException(400, "翻译尚未完成")
    if not row["result_path"] or not os.path.exists(row["result_path"]):
        raise HTTPException(410, "文件已过期")

    download_name = _download_filename(row["original_filename"])

    from fastapi.responses import FileResponse
    return FileResponse(
        row["result_path"],
        media_type=_result_media_type(row["original_filename"]),
        filename=download_name,
    )


@router.delete("/{task_id}")
async def delete_task(task_id: str, request: Request):
    """Delete a task and its files."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT original_path, result_path FROM translation_tasks WHERE id = ?",
        (task_id,),
    )
    row = await cursor.fetchone()

    if not row:
        await db.close()
        raise HTTPException(404, "任务不存在")

    upload_dir = os.path.join(UPLOAD_DIR, task_id)
    result_dir = os.path.join(RESULT_DIR, task_id)
    shutil.rmtree(upload_dir, ignore_errors=True)
    shutil.rmtree(result_dir, ignore_errors=True)

    await db.execute("DELETE FROM translation_tasks WHERE id = ?", (task_id,))
    await db.commit()
    await db.close()

    return {"message": "已删除"}


@router.post("/{task_id}/retry")
async def retry_task(task_id: str, request: Request, background_tasks: BackgroundTasks):
    """Retry a failed translation task."""
    token = _get_token(request)
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, status, token FROM translation_tasks WHERE id = ?",
        (task_id,),
    )
    row = await cursor.fetchone()
    await db.close()

    if not row:
        raise HTTPException(404, "任务不存在")
    if row["token"] != token:
        raise HTTPException(403, "无权操作")
    if row["status"] != "failed":
        raise HTTPException(400, "只能重试失败的任务")

    await _update_task(
        task_id,
        status="pending",
        progress=0,
        error_message=None,
        translated_paragraphs=0,
        total_paragraphs=0,
    )

    background_tasks.add_task(run_translation, task_id)
    return {"task_id": task_id, "status": "pending"}


async def _update_task(task_id: str, **kwargs):
    """Update task fields with short-lived DB connection."""
    db = await get_db()
    parts, params = [], []
    for k, v in kwargs.items():
        parts.append(f"{k} = ?")
        params.append(v)
    params.append(task_id)
    await db.execute(f"UPDATE translation_tasks SET {', '.join(parts)} WHERE id = ?", params)
    await db.commit()
    await db.close()

    # Broadcast via WebSocket
    from .ws import manager as ws_manager
    data = {"task_id": task_id}
    for k in ("status", "progress", "translated_paragraphs", "total_paragraphs", "error_message"):
        if k in kwargs:
            data[k] = kwargs[k]
    await ws_manager.broadcast(task_id, data)


async def run_translation(task_id: str):
    """Background task: execute the full translation pipeline."""
    from ..services.queue import translation_semaphore

    async with translation_semaphore:
        # Re-read task after acquiring semaphore (may have been deleted while waiting)
        db = await get_db()
        cursor = await db.execute(
            """SELECT original_path, glossary_id, status, use_builtin_glossary,
                      source_lang, target_lang
               FROM translation_tasks WHERE id = ?""",
            (task_id,),
        )
        row = await cursor.fetchone()
        await db.close()

        if not row or row["status"] not in ("pending", "failed"):
            return

        original_path = row["original_path"]
        glossary_id = row["glossary_id"]
        use_builtin = bool(row["use_builtin_glossary"])
        source_lang = row["source_lang"] or "zh"
        target_lang = row["target_lang"] or "en"

        try:
            ext = _file_extension(original_path)
            if ext not in SUPPORTED_FILE_EXTENSIONS:
                raise ValueError("仅支持 .docx / .xlsx 文件")

            # Step 1: Extract document units
            await _update_task(task_id, status="extracting")

            task_dir = os.path.join(UPLOAD_DIR, task_id)
            units_path = os.path.join(task_dir, "units.json")
            if ext == ".xlsx":
                data = await extract_cells(
                    original_path,
                    units_path,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
            else:
                data = await extract_paragraphs(
                    original_path,
                    units_path,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )

            units = data.get("units", [])
            if not units:
                raise ValueError("文档中没有可翻译的内容")

            # Step 2: Translate
            translations = await translate_all(
                units,
                glossary_id,
                task_id,
                use_builtin=use_builtin,
                source_lang=source_lang,
                target_lang=target_lang,
            )

            # Step 3: Build bilingual DOCX
            await _update_task(task_id, status="building")

            result_dir = os.path.join(RESULT_DIR, task_id)
            os.makedirs(result_dir, exist_ok=True)

            translations_path = os.path.join(task_dir, "translations.json")
            with open(translations_path, "w", encoding="utf-8") as f:
                json.dump({"translations": translations}, f, ensure_ascii=False)

            result_path = os.path.join(result_dir, f"translated{ext}")
            if ext == ".xlsx":
                await insert_cell_translations(
                    original_path,
                    translations_path,
                    result_path,
                    cells_json_path=units_path,
                )
                valid = await validate_xlsx(result_path)
            else:
                await insert_translations(
                    original_path,
                    translations_path,
                    result_path,
                    paragraphs_json_path=units_path,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
                valid = await validate_docx(result_path)

            # Step 4: Validate (soft check — don't abort on failure)
            if not valid:
                import logging
                logging.getLogger(__name__).warning(f"Output validation warning for task {task_id}")

            # Done
            await _update_task(
                task_id,
                status="completed",
                result_path=result_path,
                progress=100,
                error_message=None,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        except Exception as e:
            await _update_task(task_id, status="failed", error_message=str(e))
