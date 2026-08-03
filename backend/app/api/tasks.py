"""Translation task API routes."""

import json
import math
import os
import shutil
import uuid
import zipfile
from io import BytesIO

from fastapi import APIRouter, Request, Response, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from ..config import (
    MAX_FILE_SIZE,
    MAX_FILE_SIZE_LABEL,
    UPLOAD_DIR,
    UPLOAD_CHUNK_SIZE,
    RESULT_DIR,
    TASK_EXPIRE_DAYS,
    TOKEN_EXPIRE_DAYS,
    MAX_PARALLEL_TASKS,
)
from ..auth import get_current_user
from ..database import get_db
from ..services.docx_handler import extract_paragraphs, insert_translations, validate_docx
from ..services.excel_handler import extract_cells, insert_cell_translations, validate_xlsx
from ..services.pptx_handler import extract_slide_text, insert_slide_translations, validate_pptx
from ..services.translator import translate_all
from ..time_utils import local_expiry_string, local_now_string

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

SUPPORTED_FILE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
RESULT_MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
STREAM_CHUNK_SIZE = 1024 * 1024
CHUNK_UPLOAD_ROOT = os.path.join(UPLOAD_DIR, ".chunks")


class BatchDownloadRequest(BaseModel):
    task_ids: list[str]


class ChunkUploadCreateRequest(BaseModel):
    filename: str
    size: int
    source_lang: str = "zh"
    target_lang: str = "en"
    glossary_id: Optional[str] = None
    use_builtin_glossary: bool = False


class ChunkUploadCompleteRequest(BaseModel):
    upload_id: str


def _get_owner_workid(request: Request) -> str:
    return get_current_user(request).workid


def _file_extension(filename: str | None) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _chunk_upload_dir(upload_id: str) -> str:
    try:
        normalized = str(uuid.UUID(upload_id))
    except ValueError:
        raise HTTPException(400, "上传会话无效")
    return os.path.join(CHUNK_UPLOAD_ROOT, normalized)


def _chunk_metadata_path(upload_id: str) -> str:
    return os.path.join(_chunk_upload_dir(upload_id), "metadata.json")


def _chunk_part_path(upload_id: str, index: int) -> str:
    return os.path.join(_chunk_upload_dir(upload_id), f"{index:08d}.part")


def _validate_upload_metadata(data: ChunkUploadCreateRequest) -> str:
    if data.source_lang == data.target_lang:
        raise HTTPException(400, "源语言和目标语言不能相同")

    ext = _file_extension(data.filename)
    if ext not in SUPPORTED_FILE_EXTENSIONS:
        raise HTTPException(400, "仅支持 .docx / .xlsx / .pptx 文件")

    if data.size <= 0:
        raise HTTPException(400, "文件大小无效")
    if MAX_FILE_SIZE and data.size > MAX_FILE_SIZE:
        raise HTTPException(400, f"文件超过 {MAX_FILE_SIZE_LABEL} 限制")
    return ext


async def _ensure_glossary_access(glossary_id: str | None, owner_workid: str) -> None:
    if not glossary_id:
        return
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT 1 FROM glossaries
               WHERE id = ? AND (is_builtin = 1 OR owner_workid = ?)""",
            (glossary_id, owner_workid),
        )
        if not await cursor.fetchone():
            raise HTTPException(404, "术语表不存在或无权使用")
    finally:
        await db.close()


def _load_upload_metadata(upload_id: str) -> dict:
    metadata_path = _chunk_metadata_path(upload_id)
    if not os.path.exists(metadata_path):
        raise HTTPException(404, "上传会话不存在或已过期")
    with open(metadata_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_upload_metadata(upload_id: str, metadata: dict) -> None:
    os.makedirs(_chunk_upload_dir(upload_id), exist_ok=True)
    with open(_chunk_metadata_path(upload_id), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False)


async def _save_upload_file(file: UploadFile, target_path: str) -> None:
    total_size = 0
    with open(target_path, "wb") as f:
        while True:
            chunk = await file.read(STREAM_CHUNK_SIZE)
            if not chunk:
                break
            total_size += len(chunk)
            if MAX_FILE_SIZE and total_size > MAX_FILE_SIZE:
                raise HTTPException(400, f"文件超过 {MAX_FILE_SIZE_LABEL} 限制")
            f.write(chunk)


async def _save_chunk_file(file: UploadFile, target_path: str, max_chunk_size: int) -> int:
    total_size = 0
    with open(target_path, "wb") as f:
        while True:
            chunk = await file.read(STREAM_CHUNK_SIZE)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > max_chunk_size:
                raise HTTPException(400, "分片大小超过限制")
            f.write(chunk)
    return total_size


async def _check_parallel_task_limit(owner_workid: str):
    if MAX_PARALLEL_TASKS is None:
        return

    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM translation_tasks WHERE owner_workid = ? AND status NOT IN ('completed', 'failed')",
        (owner_workid,),
    )
    row = await cursor.fetchone()
    await db.close()
    if row["cnt"] >= MAX_PARALLEL_TASKS:
        raise HTTPException(429, f"最多 {MAX_PARALLEL_TASKS} 个并行任务")


async def _create_translation_task_from_file(
    *,
    owner_workid: str,
    filename: str,
    ext: str,
    original_path: str,
    source_lang: str,
    target_lang: str,
    glossary_id: Optional[str],
    use_builtin_glossary: bool,
    background_tasks: BackgroundTasks,
) -> dict:
    task_id = os.path.basename(os.path.dirname(original_path))
    created_at = local_now_string()
    expires_at = local_expiry_string(TASK_EXPIRE_DAYS)

    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO translation_tasks
            (id, token, owner_workid, original_filename, original_path, glossary_id, source_lang, target_lang,
             status, created_at, expires_at, use_builtin_glossary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (task_id, owner_workid, owner_workid, filename, original_path, glossary_id, source_lang, target_lang, created_at, expires_at,
             1 if use_builtin_glossary else 0),
        )
        await db.commit()
    finally:
        await db.close()

    background_tasks.add_task(run_translation, task_id)
    return {"task_id": task_id, "status": "pending"}


def _download_filename(original_filename: str | None) -> str:
    base_name = original_filename or "document"
    ext = _file_extension(base_name)
    if ext in SUPPORTED_FILE_EXTENSIONS:
        base_name = base_name[:-len(ext)]
    else:
        ext = ".docx"
    suffix = "翻译版" if ext == ".xlsx" else "双语"
    return f"{base_name}_{suffix}{ext}"


def _safe_zip_name(filename: str) -> str:
    return filename.replace("\\", "_").replace("/", "_").strip() or "document_双语.pptx"


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
    """Upload DOCX/XLSX/PPTX file and create translation task."""
    owner_workid = _get_owner_workid(request)

    ext = _validate_upload_metadata(
        ChunkUploadCreateRequest(
            filename=file.filename or "",
            size=1,
            source_lang=source_lang,
            target_lang=target_lang,
            glossary_id=glossary_id,
            use_builtin_glossary=use_builtin_glossary == "true",
        )
    )
    await _ensure_glossary_access(glossary_id, owner_workid)
    await _check_parallel_task_limit(owner_workid)

    # Create task
    task_id = str(uuid.uuid4())
    task_dir = os.path.join(UPLOAD_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    original_path = os.path.join(task_dir, f"original{ext}")
    try:
        await _save_upload_file(file, original_path)
    except HTTPException:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise

    try:
        return await _create_translation_task_from_file(
            owner_workid=owner_workid,
            filename=file.filename or "",
            ext=ext,
            original_path=original_path,
            source_lang=source_lang,
            target_lang=target_lang,
            glossary_id=glossary_id,
            use_builtin_glossary=use_builtin_glossary == "true",
            background_tasks=background_tasks,
        )
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise


@router.post("/uploads")
async def create_chunk_upload(request: Request, payload: ChunkUploadCreateRequest):
    """Create a chunked upload session."""
    owner_workid = _get_owner_workid(request)

    ext = _validate_upload_metadata(payload)
    await _ensure_glossary_access(payload.glossary_id, owner_workid)
    await _check_parallel_task_limit(owner_workid)

    upload_id = str(uuid.uuid4())
    chunk_size = UPLOAD_CHUNK_SIZE
    chunk_count = math.ceil(payload.size / chunk_size)
    metadata = {
        "upload_id": upload_id,
        "owner_workid": owner_workid,
        "filename": payload.filename,
        "size": payload.size,
        "ext": ext,
        "source_lang": payload.source_lang,
        "target_lang": payload.target_lang,
        "glossary_id": payload.glossary_id,
        "use_builtin_glossary": payload.use_builtin_glossary,
        "chunk_size": chunk_size,
        "chunk_count": chunk_count,
        "created_at": local_now_string(),
    }
    _write_upload_metadata(upload_id, metadata)

    return {
        "upload_id": upload_id,
        "chunk_size": chunk_size,
        "chunk_count": chunk_count,
    }


@router.post("/uploads/{upload_id}/chunks")
async def upload_chunk(
    upload_id: str,
    request: Request,
    index: int = Form(...),
    file: UploadFile = File(...),
):
    """Upload one chunk for a chunked upload session."""
    owner_workid = _get_owner_workid(request)
    metadata = _load_upload_metadata(upload_id)
    if metadata["owner_workid"] != owner_workid:
        raise HTTPException(403, "无权操作该上传会话")

    chunk_count = metadata["chunk_count"]
    if index < 0 or index >= chunk_count:
        raise HTTPException(400, "分片序号无效")

    expected_size = metadata["chunk_size"]
    if index == chunk_count - 1:
        expected_size = metadata["size"] - metadata["chunk_size"] * (chunk_count - 1)

    part_path = _chunk_part_path(upload_id, index)
    tmp_path = f"{part_path}.tmp"
    try:
        actual_size = await _save_chunk_file(file, tmp_path, expected_size)
        if actual_size != expected_size:
            raise HTTPException(400, "分片大小不匹配")
        os.replace(tmp_path, part_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    uploaded_chunks = sum(
        1 for i in range(chunk_count)
        if os.path.exists(_chunk_part_path(upload_id, i))
    )
    return {
        "upload_id": upload_id,
        "index": index,
        "uploaded_chunks": uploaded_chunks,
        "chunk_count": chunk_count,
    }


@router.post("/uploads/{upload_id}/complete")
async def complete_chunk_upload(
    upload_id: str,
    payload: ChunkUploadCompleteRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Merge chunks and create a translation task."""
    if payload.upload_id != upload_id:
        raise HTTPException(400, "上传会话不匹配")

    owner_workid = _get_owner_workid(request)
    metadata = _load_upload_metadata(upload_id)
    if metadata["owner_workid"] != owner_workid:
        raise HTTPException(403, "无权操作该上传会话")
    if MAX_FILE_SIZE and metadata["size"] > MAX_FILE_SIZE:
        raise HTTPException(400, f"文件超过 {MAX_FILE_SIZE_LABEL} 限制")

    await _ensure_glossary_access(metadata["glossary_id"], owner_workid)
    await _check_parallel_task_limit(owner_workid)

    chunk_count = metadata["chunk_count"]
    for index in range(chunk_count):
        part_path = _chunk_part_path(upload_id, index)
        if not os.path.exists(part_path):
            raise HTTPException(400, "上传分片不完整")
        expected_size = metadata["chunk_size"]
        if index == chunk_count - 1:
            expected_size = metadata["size"] - metadata["chunk_size"] * (chunk_count - 1)
        if os.path.getsize(part_path) != expected_size:
            raise HTTPException(400, "上传分片大小不匹配")

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(UPLOAD_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    original_path = os.path.join(task_dir, f"original{metadata['ext']}")

    try:
        total_size = 0
        with open(original_path, "wb") as target:
            for index in range(chunk_count):
                with open(_chunk_part_path(upload_id, index), "rb") as source:
                    while True:
                        chunk = source.read(STREAM_CHUNK_SIZE)
                        if not chunk:
                            break
                        total_size += len(chunk)
                        if MAX_FILE_SIZE and total_size > MAX_FILE_SIZE:
                            raise HTTPException(400, f"文件超过 {MAX_FILE_SIZE_LABEL} 限制")
                        target.write(chunk)

        if total_size != metadata["size"]:
            raise HTTPException(400, "合并后的文件大小不匹配")

        result = await _create_translation_task_from_file(
            owner_workid=owner_workid,
            filename=metadata["filename"],
            ext=metadata["ext"],
            original_path=original_path,
            source_lang=metadata["source_lang"],
            target_lang=metadata["target_lang"],
            glossary_id=metadata["glossary_id"],
            use_builtin_glossary=metadata["use_builtin_glossary"],
            background_tasks=background_tasks,
        )
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise

    shutil.rmtree(_chunk_upload_dir(upload_id), ignore_errors=True)
    return result


@router.get("")
async def list_tasks(request: Request, page: int = 1, page_size: int = 20):
    """List tasks owned by the current authenticated user."""
    owner_workid = _get_owner_workid(request)

    db = await get_db()
    offset = (page - 1) * page_size

    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM translation_tasks WHERE owner_workid = ?",
        (owner_workid,),
    )
    total = (await cursor.fetchone())["cnt"]

    cursor = await db.execute(
        """SELECT id, original_filename, source_lang, target_lang, status, progress,
                  total_paragraphs, translated_paragraphs, created_at, started_at, completed_at
           FROM translation_tasks WHERE owner_workid = ?
           ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        (owner_workid, page_size, offset),
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
        "started_at": r["started_at"],
        "completed_at": r["completed_at"],
    } for r in rows]

    return {"total": total, "items": items}


@router.get("/statistics")
async def get_task_statistics(request: Request):
    """Return anonymous, platform-wide translation summary metrics."""
    _get_owner_workid(request)
    today = local_now_string()[:10]
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT completed_documents FROM translation_statistics WHERE id = 1"
        )
        total_row = await cursor.fetchone()

        cursor = await db.execute(
            "SELECT completed_documents FROM translation_daily_statistics WHERE stat_date = ?",
            (today,),
        )
        today_row = await cursor.fetchone()

        cursor = await db.execute(
            """SELECT
                   COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
                   COALESCE(SUM(CASE WHEN status IN ('extracting', 'translating', 'building') THEN 1 ELSE 0 END), 0) AS executing,
                   COALESCE(SUM(CASE WHEN status IN ('pending', 'extracting', 'translating', 'building') THEN 1 ELSE 0 END), 0) AS in_progress,
                   COALESCE(SUM(CASE WHEN substr(created_at, 1, 10) = ? THEN 1 ELSE 0 END), 0) AS submitted_today
               FROM translation_tasks""",
            (today,),
        )
        task_counts = await cursor.fetchone()
    finally:
        await db.close()

    return {
        "completed_total": total_row["completed_documents"] if total_row else 0,
        "completed_today": today_row["completed_documents"] if today_row else 0,
        "pending": task_counts["pending"],
        "executing": task_counts["executing"],
        "in_progress": task_counts["in_progress"],
        "submitted_today": task_counts["submitted_today"],
    }


@router.post("/batch-download")
async def batch_download_tasks(payload: BatchDownloadRequest, request: Request):
    """Download multiple completed translated files as a ZIP archive."""
    owner_workid = _get_owner_workid(request)
    task_ids = list(dict.fromkeys(payload.task_ids))
    if not task_ids:
        raise HTTPException(400, "请选择要下载的任务")

    placeholders = ",".join("?" for _ in task_ids)
    db = await get_db()
    cursor = await db.execute(
        f"""SELECT id, result_path, status, original_filename
            FROM translation_tasks
            WHERE owner_workid = ? AND id IN ({placeholders})""",
        (owner_workid, *task_ids),
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
    owner_workid = _get_owner_workid(request)
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, status, progress, total_paragraphs, translated_paragraphs,
                  error_message, created_at, started_at, completed_at
           FROM translation_tasks WHERE id = ? AND owner_workid = ?""",
        (task_id, owner_workid),
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
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }


@router.get("/{task_id}/download")
async def download_task(task_id: str, request: Request):
    """Download translated document."""
    owner_workid = _get_owner_workid(request)
    db = await get_db()
    cursor = await db.execute(
        "SELECT result_path, status, original_filename FROM translation_tasks WHERE id = ? AND owner_workid = ?",
        (task_id, owner_workid),
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
    owner_workid = _get_owner_workid(request)
    db = await get_db()
    cursor = await db.execute(
        "SELECT original_path, result_path FROM translation_tasks WHERE id = ? AND owner_workid = ?",
        (task_id, owner_workid),
    )
    row = await cursor.fetchone()

    if not row:
        await db.close()
        raise HTTPException(404, "任务不存在")

    upload_dir = os.path.join(UPLOAD_DIR, task_id)
    result_dir = os.path.join(RESULT_DIR, task_id)
    shutil.rmtree(upload_dir, ignore_errors=True)
    shutil.rmtree(result_dir, ignore_errors=True)

    await db.execute("DELETE FROM translation_tasks WHERE id = ? AND owner_workid = ?", (task_id, owner_workid))
    await db.commit()
    await db.close()

    return {"message": "已删除"}


@router.post("/{task_id}/retry")
async def retry_task(task_id: str, request: Request, background_tasks: BackgroundTasks):
    """Retry a failed translation task."""
    owner_workid = _get_owner_workid(request)
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, status FROM translation_tasks WHERE id = ? AND owner_workid = ?",
        (task_id, owner_workid),
    )
    row = await cursor.fetchone()
    await db.close()

    if not row:
        raise HTTPException(404, "任务不存在")
    if row["status"] != "failed":
        raise HTTPException(400, "只能重试失败的任务")

    await _update_task(
        task_id,
        status="pending",
        progress=0,
        error_message=None,
        translated_paragraphs=0,
        total_paragraphs=0,
        started_at=None,
        completed_at=None,
        result_path=None,
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
    for k in (
        "status",
        "progress",
        "translated_paragraphs",
        "total_paragraphs",
        "error_message",
        "started_at",
        "completed_at",
    ):
        if k in kwargs:
            data[k] = kwargs[k]
    await ws_manager.broadcast(task_id, data)


async def _complete_task_and_record_statistics(
    task_id: str,
    result_path: str,
    completed_at: str,
) -> bool:
    """Mark a task complete and update permanent counters exactly once."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """UPDATE translation_tasks
               SET status = 'completed', result_path = ?, progress = 100,
                   error_message = NULL, completed_at = ?
               WHERE id = ? AND status != 'completed'""",
            (result_path, completed_at, task_id),
        )
        if cursor.rowcount != 1:
            await db.rollback()
            return False

        await db.execute(
            """INSERT INTO translation_statistics (id, completed_documents)
               VALUES (1, 1)
               ON CONFLICT(id) DO UPDATE SET
                   completed_documents = completed_documents + 1"""
        )
        await db.execute(
            """INSERT INTO translation_daily_statistics (stat_date, completed_documents)
               VALUES (?, 1)
               ON CONFLICT(stat_date) DO UPDATE SET
                   completed_documents = completed_documents + 1""",
            (completed_at[:10],),
        )
        await db.commit()
    finally:
        await db.close()

    from .ws import manager as ws_manager
    await ws_manager.broadcast(task_id, {
        "task_id": task_id,
        "status": "completed",
        "result_path": result_path,
        "progress": 100,
        "error_message": None,
        "completed_at": completed_at,
    })
    return True


async def run_translation(task_id: str):
    """Background task: execute the full translation pipeline."""
    from ..services.queue import translation_slot

    async with translation_slot():
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
                raise ValueError("仅支持 .docx / .xlsx / .pptx 文件")

            # Step 1: Extract document units. This marks the true processing start after queue wait.
            await _update_task(task_id, status="extracting", started_at=local_now_string())

            task_dir = os.path.join(UPLOAD_DIR, task_id)
            units_path = os.path.join(task_dir, "units.json")
            if ext == ".xlsx":
                data = await extract_cells(
                    original_path,
                    units_path,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
            elif ext == ".pptx":
                data = await extract_slide_text(
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
            elif ext == ".pptx":
                await insert_slide_translations(
                    original_path,
                    translations_path,
                    result_path,
                    units_json_path=units_path,
                )
                valid = await validate_pptx(result_path)
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

            # Step 4: Validate. PPTX failures are hard errors because PowerPoint may require repair on open.
            if not valid:
                import logging
                logging.getLogger(__name__).warning(f"Output validation warning for task {task_id}")
                if ext == ".pptx":
                    raise RuntimeError("生成的 PPTX 文件结构校验失败，请重试或联系管理员")

            # Done
            await _complete_task_and_record_statistics(
                task_id,
                result_path,
                local_now_string(),
            )

        except Exception as e:
            await _update_task(
                task_id,
                status="failed",
                error_message=str(e),
                completed_at=local_now_string(),
            )
