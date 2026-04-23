"""Language list API."""

from fastapi import APIRouter

from ..config import SUPPORTED_LANGUAGES

router = APIRouter(prefix="/api", tags=["languages"])


@router.get("/languages")
async def list_languages():
    return SUPPORTED_LANGUAGES
