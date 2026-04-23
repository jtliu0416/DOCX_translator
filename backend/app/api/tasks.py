"""Translation task API routes."""

import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response, UploadFile, File, Form, HTTPException, BackgroundTasks
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
from ..services.translator import translate_all

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _get_token(request: Request) -> str:
    # Middleware always sets this
    return getattr(request.state, "token", "") or request.cookies.get("token", "")


@router.post("")
async def create_task(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_lang: str = Form("zh"),
    target_lang: str = Form("en"),
    glossary_id: Optional[str] = Form(None),
):
    """Upload DOCX file and create translation task."""
    # Token from middleware
    token = _get_token(request)
    if not token:
        raise HTTPException(401, "Token missing")

    # Validate file
    if not file.filename.endswith(".docx"):
        raise HTTPException(400, "仅支持 .docx 文件")

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

    original_path = os.path.join(task_dir, "original.docx")
    with open(original_path, "wb") as f:
        f.write(content)

    expires_at = (datetime.now(timezone.utc) + timedelta(days=TASK_EXPIRE_DAYS)).isoformat()

    await db.execute(
        """INSERT INTO translation_tasks
        (id, token, original_filename, original_path, glossary_id, source_lang, target_lang, status, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (task_id, token, file.filename, original_path, glossary_id, source_lang, target_lang, expires_at),
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
    """Download translated DOCX."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT result_path, status FROM translation_tasks WHERE id = ?",
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

    from fastapi.responses import FileResponse
    return FileResponse(
        row["result_path"],
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=os.path.basename(row["result_path"]),
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


async def run_translation(task_id: str):
    """Background task: execute the full translation pipeline."""
    # Read task info
    db = await get_db()
    cursor = await db.execute(
        "SELECT original_path, glossary_id FROM translation_tasks WHERE id = ?",
        (task_id,),
    )
    row = await cursor.fetchone()
    await db.close()

    if not row:
        return

    original_path = row["original_path"]
    glossary_id = row["glossary_id"]

    try:
        # Step 1: Extract paragraphs
        await _update_task(task_id, status="extracting")

        task_dir = os.path.join(UPLOAD_DIR, task_id)
        paragraphs_path = os.path.join(task_dir, "paragraphs.json")
        data = await extract_paragraphs(original_path, paragraphs_path)

        units = data.get("units", [])
        if not units:
            raise ValueError("文档中没有可翻译的内容")

        # Step 2: Translate
        translations = await translate_all(units, glossary_id, task_id)

        # Step 3: Build bilingual DOCX
        await _update_task(task_id, status="building")

        result_dir = os.path.join(RESULT_DIR, task_id)
        os.makedirs(result_dir, exist_ok=True)

        translations_path = os.path.join(task_dir, "translations.json")
        with open(translations_path, "w", encoding="utf-8") as f:
            json.dump({"translations": translations}, f, ensure_ascii=False)

        result_path = os.path.join(result_dir, "translated.docx")
        paragraphs_path = os.path.join(task_dir, "paragraphs.json")
        await insert_translations(original_path, translations_path, result_path,
                                  paragraphs_json_path=paragraphs_path)

        # Step 4: Validate (soft check — don't abort on failure)
        valid = await validate_docx(result_path)
        if not valid:
            import logging
            logging.getLogger(__name__).warning(f"DOCX validation warning for task {task_id}")

        # Done
        await _update_task(
            task_id,
            status="completed",
            result_path=result_path,
            progress=100,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as e:
        await _update_task(task_id, status="failed", error_message=str(e))
