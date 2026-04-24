"""Global translation queue control."""
import asyncio

from ..config import MAX_CONCURRENT_TRANSLATIONS

translation_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TRANSLATIONS)
