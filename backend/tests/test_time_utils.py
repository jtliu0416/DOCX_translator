import sys
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.time_utils import local_now_string, utc_timestamp_to_local_string  # noqa: E402


class TimeUtilsTest(TestCase):
    def test_local_now_string_uses_database_format(self) -> None:
        self.assertRegex(local_now_string(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_sqlite_utc_timestamp_converts_to_local_time(self) -> None:
        self.assertEqual(
            utc_timestamp_to_local_string("2026-07-07 13:30:37"),
            "2026-07-07 21:30:37",
        )

    def test_utc_iso_timestamp_converts_to_local_time(self) -> None:
        self.assertEqual(
            utc_timestamp_to_local_string("2026-07-14T06:52:34.732307+00:00"),
            "2026-07-14 14:52:34",
        )
