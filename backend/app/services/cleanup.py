"""Scheduled cleanup of expired tasks and files."""

import os
import shutil
from datetime import datetime, timedelta

from ..config import TASK_EXPIRE_DAYS, UPLOAD_DIR, RESULT_DIR, GLOSSARY_DIR
from ..database import get_db


async def cleanup_expired():
    """Delete expired tasks and their associated files."""
    db = await get_db()
    cutoff = (datetime.utcnow() - timedelta(days=TASK_EXPIRE_DAYS)).isoformat()

    cursor = await db.execute(
        "SELECT id, original_path, result_path FROM translation_tasks WHERE expires_at < ?",
        (cutoff,),
    )
    expired = await cursor.fetchall()

    for row in expired:
        task_id = row["id"]
        # Remove upload directory
        upload_dir = os.path.join(UPLOAD_DIR, task_id)
        if os.path.isdir(upload_dir):
            shutil.rmtree(upload_dir, ignore_errors=True)
        # Remove result directory
        result_dir = os.path.join(RESULT_DIR, task_id)
        if os.path.isdir(result_dir):
            shutil.rmtree(result_dir, ignore_errors=True)

    await db.execute("DELETE FROM translation_tasks WHERE expires_at < ?", (cutoff,))
    await db.commit()
    await db.close()

    return len(expired)
