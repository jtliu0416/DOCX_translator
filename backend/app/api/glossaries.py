"""Glossary management API routes."""

import os
import shutil
import uuid

from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from typing import Optional

from ..config import GLOSSARY_DIR
from ..database import get_db
from ..services.glossary import parse_glossary_file, save_glossary_terms

router = APIRouter(prefix="/api/glossaries", tags=["glossaries"])


def _get_token(request: Request) -> str:
    return getattr(request.state, "token", "") or request.cookies.get("token", "")


@router.post("")
async def create_glossary(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    source_lang: str = Form("zh"),
    target_lang: str = Form("en"),
):
    """Upload a glossary file."""
    token = _get_token(request)
    if not token:
        raise HTTPException(401, "请先访问首页")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".csv", ".xlsx", ".txt"):
        raise HTTPException(400, "仅支持 CSV/XLSX/TXT 格式")

    glossary_id = str(uuid.uuid4())
    glossary_dir = os.path.join(GLOSSARY_DIR, glossary_id)
    os.makedirs(glossary_dir, exist_ok=True)

    file_path = os.path.join(glossary_dir, f"original{ext}")
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # Parse and save terms
    try:
        terms = parse_glossary_file(file_path)
    except Exception as e:
        shutil.rmtree(glossary_dir, ignore_errors=True)
        raise HTTPException(400, f"术语表解析失败: {e}")

    db = await get_db()
    await db.execute(
        """INSERT INTO glossaries (id, token, name, source_lang, target_lang, file_path, term_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (glossary_id, token, name, source_lang, target_lang, file_path, len(terms)),
    )
    await db.commit()
    await db.close()

    await save_glossary_terms(glossary_id, terms)

    return {"glossary_id": glossary_id, "name": name, "term_count": len(terms)}


@router.get("")
async def list_glossaries(request: Request):
    """List glossaries for current token."""
    token = _get_token(request)
    if not token:
        return []

    db = await get_db()
    cursor = await db.execute(
        "SELECT id, name, source_lang, target_lang, term_count, created_at, is_builtin FROM glossaries WHERE token = ? OR is_builtin = 1 ORDER BY is_builtin DESC, created_at DESC",
        (token,),
    )
    rows = await cursor.fetchall()
    await db.close()

    return [{
        "id": r["id"],
        "name": r["name"],
        "source_lang": r["source_lang"],
        "target_lang": r["target_lang"],
        "term_count": r["term_count"],
        "created_at": r["created_at"],
        "is_builtin": bool(r["is_builtin"]),
    } for r in rows]


@router.get("/{glossary_id}")
async def get_glossary(glossary_id: str, request: Request):
    """Get glossary detail with term preview."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, name, source_lang, target_lang, term_count, created_at, is_builtin FROM glossaries WHERE id = ?",
        (glossary_id,),
    )
    row = await cursor.fetchone()

    if not row:
        await db.close()
        raise HTTPException(404, "术语表不存在")

    is_builtin = bool(row["is_builtin"])

    if is_builtin:
        cursor = await db.execute(
            "SELECT source_term, target_term, note FROM glossary_terms WHERE glossary_id = ?",
            (glossary_id,),
        )
    else:
        cursor = await db.execute(
            "SELECT source_term, target_term, note FROM glossary_terms WHERE glossary_id = ? LIMIT 50",
            (glossary_id,),
        )
    terms = await cursor.fetchall()
    await db.close()

    return {
        "id": row["id"],
        "name": row["name"],
        "source_lang": row["source_lang"],
        "target_lang": row["target_lang"],
        "term_count": row["term_count"],
        "created_at": row["created_at"],
        "is_builtin": is_builtin,
        "terms": [{
            "source_term": t["source_term"],
            "target_term": t["target_term"],
            "note": t["note"],
        } for t in terms],
    }


@router.delete("/{glossary_id}")
async def delete_glossary(glossary_id: str, request: Request):
    """Delete a glossary and its file."""
    db = await get_db()
    cursor = await db.execute("SELECT file_path, is_builtin FROM glossaries WHERE id = ?", (glossary_id,))
    row = await cursor.fetchone()

    if not row:
        await db.close()
        raise HTTPException(404, "术语表不存在")

    if row["is_builtin"]:
        await db.close()
        raise HTTPException(403, "内置术语表不可删除")

    glossary_dir = os.path.join(GLOSSARY_DIR, glossary_id)
    shutil.rmtree(glossary_dir, ignore_errors=True)

    await db.execute("DELETE FROM glossary_terms WHERE glossary_id = ?", (glossary_id,))
    await db.execute("DELETE FROM glossaries WHERE id = ?", (glossary_id,))
    await db.commit()
    await db.close()

    return {"message": "已删除"}
