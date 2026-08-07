"""LLM settings API routes (read-only, config from .env file)."""

from fastapi import APIRouter

from ..config import (
    MAX_CONCURRENT_TRANSLATIONS,
    MAX_FILE_SIZE,
    MAX_FILE_SIZE_LABEL,
    MAX_PARALLEL_TASKS,
    TRANSLATION_BATCH_CONCURRENCY,
    UPLOAD_CHUNK_SIZE,
    UPLOAD_CHUNK_SIZE_LABEL,
    llm,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/llm")
async def get_llm_settings():
    return {
        "provider": llm.provider,
        "api_url": llm.api_url,
        "api_key_set": bool(llm.api_key),
        "model": llm.model,
    }


@router.get("/upload")
async def get_upload_settings():
    return {
        "max_file_size": MAX_FILE_SIZE,
        "max_file_size_label": MAX_FILE_SIZE_LABEL,
        "chunk_size": UPLOAD_CHUNK_SIZE,
        "chunk_size_label": UPLOAD_CHUNK_SIZE_LABEL,
    }


@router.get("/concurrency")
async def get_concurrency_settings():
    max_simultaneous_llm_requests = (
        None
        if MAX_CONCURRENT_TRANSLATIONS is None
        else MAX_CONCURRENT_TRANSLATIONS * TRANSLATION_BATCH_CONCURRENCY
    )
    return {
        "max_parallel_tasks_per_token": MAX_PARALLEL_TASKS,
        "max_concurrent_translations": MAX_CONCURRENT_TRANSLATIONS,
        "translation_batch_concurrency": TRANSLATION_BATCH_CONCURRENCY,
        "max_simultaneous_llm_requests": max_simultaneous_llm_requests,
        "upload_chunk_size": UPLOAD_CHUNK_SIZE,
        "upload_chunk_size_label": UPLOAD_CHUNK_SIZE_LABEL,
        "frontend_upload_mode": "serial_files_and_chunks",
    }
