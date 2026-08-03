import asyncio
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase

import jwt
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth, database, main  # noqa: E402
from app.api import tasks as tasks_api  # noqa: E402
from app.time_utils import local_now_string  # noqa: E402


class JwtApiAccessTest(TestCase):
    secret = "a" * 32
    issuer = "non-gmp-lims"
    audience = "web-ui"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_auth = (auth.JWT_SECRET, auth.JWT_ISSUER, auth.JWT_AUDIENCE)
        self.original_database_path = database.DATABASE_PATH
        auth.JWT_SECRET = self.secret
        auth.JWT_ISSUER = self.issuer
        auth.JWT_AUDIENCE = self.audience
        database.DATABASE_PATH = str(Path(self.temp_dir.name) / "doctrans.db")
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        database.DATABASE_PATH = self.original_database_path
        auth.JWT_SECRET, auth.JWT_ISSUER, auth.JWT_AUDIENCE = self.original_auth
        self.temp_dir.cleanup()

    def headers_for(self, workid: str) -> dict[str, str]:
        token = jwt.encode(
            {
                "workid": workid,
                "cnname": "Test User",
                "depart": "IT",
                "username": "test.user",
                "role": "user",
                "iss": self.issuer,
                "aud": self.audience,
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            self.secret,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def insert_task(
        self,
        task_id: str,
        owner_workid: str,
        status: str = "pending",
        created_at: str | None = None,
    ) -> None:
        async def insert() -> None:
            db = await database.get_db()
            try:
                await db.execute(
                    """INSERT INTO translation_tasks
                    (id, token, owner_workid, original_filename, original_path, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (task_id, owner_workid, owner_workid, "source.docx", "missing.docx", status, created_at or local_now_string()),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(insert())

    def test_api_requires_valid_bearer_jwt(self) -> None:
        self.assertEqual(self.client.get("/api/tasks").status_code, 401)
        self.assertEqual(self.client.get("/api/tasks", headers=self.headers_for("W1001")).status_code, 200)

    def test_task_detail_is_isolated_by_workid(self) -> None:
        self.insert_task("task-owned-by-a", "A100")
        denied = self.client.get("/api/tasks/task-owned-by-a", headers=self.headers_for("B200"))
        allowed = self.client.get("/api/tasks/task-owned-by-a", headers=self.headers_for("A100"))
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(allowed.status_code, 200)

    def test_statistics_requires_authentication_and_is_anonymous(self) -> None:
        self.insert_task("active-a", "A100")
        self.insert_task("active-b", "B200", status="translating")

        self.assertEqual(self.client.get("/api/tasks/statistics").status_code, 401)
        response_a = self.client.get("/api/tasks/statistics", headers=self.headers_for("A100"))
        response_b = self.client.get("/api/tasks/statistics", headers=self.headers_for("B200"))

        self.assertEqual(response_a.status_code, 200)
        self.assertEqual(response_a.json(), response_b.json())
        self.assertEqual(response_a.json(), {
            "completed_total": 0,
            "completed_today": 0,
            "pending": 1,
            "executing": 1,
            "in_progress": 2,
            "submitted_today": 2,
        })

    def test_completed_statistics_persist_after_delete_and_are_idempotent(self) -> None:
        self.insert_task("retry-task", "A100", status="failed")
        self.assertEqual(
            self.client.post("/api/tasks/retry-task/retry", headers=self.headers_for("A100")).status_code,
            200,
        )

        completed_at = local_now_string()
        self.assertTrue(asyncio.run(tasks_api._complete_task_and_record_statistics(
            "retry-task", "result.docx", completed_at,
        )))
        self.assertFalse(asyncio.run(tasks_api._complete_task_and_record_statistics(
            "retry-task", "result.docx", completed_at,
        )))

        before_delete = self.client.get("/api/tasks/statistics", headers=self.headers_for("B200"))
        self.assertEqual(before_delete.json(), {
            "completed_total": 1,
            "completed_today": 1,
            "pending": 0,
            "executing": 0,
            "in_progress": 0,
            "submitted_today": 1,
        })

        self.assertEqual(
            self.client.delete("/api/tasks/retry-task", headers=self.headers_for("A100")).status_code,
            200,
        )
        after_delete = self.client.get("/api/tasks/statistics", headers=self.headers_for("A100"))
        self.assertEqual(after_delete.json()["completed_total"], before_delete.json()["completed_total"])
        self.assertEqual(after_delete.json()["completed_today"], before_delete.json()["completed_today"])
        self.assertEqual(after_delete.json()["submitted_today"], 0)
