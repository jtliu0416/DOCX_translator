"""Scheduled cleanup of expired tasks and files."""

import os
import shutil
from datetime import datetime, timedelta

from ..config import TASK_EXPIRE_DAYS, UPLOAD_DIR, RESULT_DIR, GLOSSARY_DIR
from ..database import get_db
from ..time_utils import app_timezone, local_cutoff_string, local_now


async def cleanup_expired():
    """Delete expired tasks and their associated files."""
    db = await get_db()
    cutoff = local_cutoff_string(TASK_EXPIRE_DAYS)

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

    chunk_root = os.path.join(UPLOAD_DIR, ".chunks")
    chunk_cutoff = local_now() - timedelta(days=1)
    if os.path.isdir(chunk_root):
        for name in os.listdir(chunk_root):
            chunk_dir = os.path.join(chunk_root, name)
            if not os.path.isdir(chunk_dir):
                continue
            try:
                modified_at = datetime.fromtimestamp(os.path.getmtime(chunk_dir), tz=app_timezone())
            except OSError:
                continue
            if modified_at < chunk_cutoff:
                shutil.rmtree(chunk_dir, ignore_errors=True)

    return len(expired)
