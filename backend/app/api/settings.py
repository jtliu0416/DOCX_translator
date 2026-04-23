"""LLM settings API routes (read-only, config from .env file)."""

from fastapi import APIRouter

from ..config import llm

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/llm")
async def get_llm_settings():
    return {
        "provider": llm.provider,
        "api_url": llm.api_url,
        "api_key_set": bool(llm.api_key),
        "model": llm.model,
    }
