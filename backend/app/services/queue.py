"""Global translation queue control."""
import asyncio
from contextlib import asynccontextmanager

from ..config import MAX_CONCURRENT_TRANSLATIONS

translation_semaphore = (
    asyncio.Semaphore(MAX_CONCURRENT_TRANSLATIONS)
    if MAX_CONCURRENT_TRANSLATIONS is not None
    else None
)


@asynccontextmanager
async def translation_slot():
    if translation_semaphore is None:
        yield
        return

    async with translation_semaphore:
        yield
