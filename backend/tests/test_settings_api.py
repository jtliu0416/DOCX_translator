import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.settings import get_concurrency_settings  # noqa: E402


class SettingsApiTest(IsolatedAsyncioTestCase):
    async def test_concurrency_settings_include_effective_defaults(self) -> None:
        payload = await get_concurrency_settings()

        self.assertEqual(payload["max_parallel_tasks_per_token"], 20)
        self.assertEqual(payload["max_concurrent_translations"], 2)
        self.assertEqual(payload["translation_batch_concurrency"], 3)
        self.assertEqual(payload["max_simultaneous_llm_requests"], 6)
        self.assertEqual(payload["frontend_upload_mode"], "serial_files_and_chunks")
