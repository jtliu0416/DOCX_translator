import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

import aiosqlite

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import (  # noqa: E402
    MIGRATIONS,
    SCHEMA,
    initialize_translation_statistics,
    migrate_utc_timestamps_to_local,
)


class LocalTimeMigrationTest(IsolatedAsyncioTestCase):
    async def test_utc_timestamp_migration_is_idempotent(self) -> None:
        db = await aiosqlite.connect(":memory:")
        db.row_factory = aiosqlite.Row
        try:
            await db.executescript(
                """
                CREATE TABLE translation_tasks (
                    id TEXT PRIMARY KEY,
                    created_at TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    expires_at TIMESTAMP
                );
                CREATE TABLE glossaries (
                    id TEXT PRIMARY KEY,
                    created_at TIMESTAMP
                );
                """
            )
            await db.execute(
                """INSERT INTO translation_tasks
                (id, created_at, started_at, completed_at, expires_at)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    "task-1",
                    "2026-07-07 13:30:37",
                    "2026-07-07T13:45:00+00:00",
                    "2026-07-07T14:00:00+00:00",
                    "2026-07-14T06:52:34.732307+00:00",
                ),
            )
            await db.execute(
                "INSERT INTO glossaries (id, created_at) VALUES (?, ?)",
                ("glossary-1", "2026-07-07 13:31:00"),
            )
            await db.commit()

            self.assertTrue(await migrate_utc_timestamps_to_local(db))
            await db.commit()

            cursor = await db.execute(
                "SELECT created_at, started_at, completed_at, expires_at FROM translation_tasks WHERE id = ?",
                ("task-1",),
            )
            task_row = await cursor.fetchone()
            self.assertEqual(task_row["created_at"], "2026-07-07 21:30:37")
            self.assertEqual(task_row["started_at"], "2026-07-07 21:45:00")
            self.assertEqual(task_row["completed_at"], "2026-07-07 22:00:00")
            self.assertEqual(task_row["expires_at"], "2026-07-14 14:52:34")

            cursor = await db.execute(
                "SELECT created_at FROM glossaries WHERE id = ?",
                ("glossary-1",),
            )
            glossary_row = await cursor.fetchone()
            self.assertEqual(glossary_row["created_at"], "2026-07-07 21:31:00")

            self.assertFalse(await migrate_utc_timestamps_to_local(db))
            await db.commit()

            cursor = await db.execute(
                "SELECT created_at, started_at, completed_at, expires_at FROM translation_tasks WHERE id = ?",
                ("task-1",),
            )
            repeated_row = await cursor.fetchone()
            self.assertEqual(dict(repeated_row), dict(task_row))

        finally:
            await db.close()

    def test_owner_workid_migrations_are_registered(self) -> None:
        self.assertIn("ALTER TABLE translation_tasks ADD COLUMN owner_workid TEXT", MIGRATIONS)
        self.assertIn("ALTER TABLE glossaries ADD COLUMN owner_workid TEXT", MIGRATIONS)

    async def test_statistics_initialize_from_existing_completed_tasks_once(self) -> None:
        db = await aiosqlite.connect(":memory:")
        db.row_factory = aiosqlite.Row
        try:
            await db.executescript(SCHEMA)
            await db.execute(
                """INSERT INTO translation_tasks
                (id, token, original_filename, original_path, status, completed_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                ("completed-task", "token", "source.docx", "source.docx", "completed", "2026-07-27 09:00:00"),
            )
            await db.commit()

            self.assertTrue(await initialize_translation_statistics(db))
            self.assertFalse(await initialize_translation_statistics(db))

            cursor = await db.execute(
                "SELECT completed_documents FROM translation_statistics WHERE id = 1"
            )
            self.assertEqual((await cursor.fetchone())["completed_documents"], 1)
            cursor = await db.execute(
                "SELECT completed_documents FROM translation_daily_statistics WHERE stat_date = ?",
                ("2026-07-27",),
            )
            self.assertEqual((await cursor.fetchone())["completed_documents"], 1)
        finally:
            await db.close()
