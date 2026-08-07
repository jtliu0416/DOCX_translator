import aiosqlite
import os
from .config import DATABASE_PATH
from .time_utils import local_now_string, utc_timestamp_to_local_string

SCHEMA = """
CREATE TABLE IF NOT EXISTS translation_tasks (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    owner_workid TEXT,
    original_filename TEXT NOT NULL,
    original_path TEXT NOT NULL,
    result_path TEXT,
    glossary_id TEXT,
    source_lang TEXT DEFAULT 'zh',
    target_lang TEXT DEFAULT 'en',
    status TEXT DEFAULT 'pending',
    progress INTEGER DEFAULT 0,
    total_paragraphs INTEGER DEFAULT 0,
    translated_paragraphs INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS glossaries (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    owner_workid TEXT,
    name TEXT NOT NULL,
    source_lang TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    file_path TEXT NOT NULL,
    term_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS glossary_terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    glossary_id TEXT NOT NULL,
    source_term TEXT NOT NULL,
    target_term TEXT NOT NULL,
    note TEXT,
    FOREIGN KEY (glossary_id) REFERENCES glossaries(id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_token ON translation_tasks(token);
CREATE INDEX IF NOT EXISTS idx_tasks_expires ON translation_tasks(expires_at);
CREATE INDEX IF NOT EXISTS idx_glossaries_token ON glossaries(token);
CREATE INDEX IF NOT EXISTS idx_terms_glossary ON glossary_terms(glossary_id);

CREATE TABLE IF NOT EXISTS translation_statistics (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    completed_documents INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS translation_daily_statistics (
    stat_date TEXT PRIMARY KEY,
    completed_documents INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

"""


async def get_db() -> aiosqlite.Connection:
    db_dir = os.path.dirname(DATABASE_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    return db


MIGRATIONS = [
    "ALTER TABLE glossaries ADD COLUMN is_builtin INTEGER DEFAULT 0",
    "ALTER TABLE translation_tasks ADD COLUMN use_builtin_glossary INTEGER DEFAULT 0",
    "ALTER TABLE translation_tasks ADD COLUMN started_at TIMESTAMP",
    "ALTER TABLE translation_tasks ADD COLUMN owner_workid TEXT",
    "ALTER TABLE glossaries ADD COLUMN owner_workid TEXT",
    "CREATE INDEX IF NOT EXISTS idx_tasks_owner_workid ON translation_tasks(owner_workid)",
    "CREATE INDEX IF NOT EXISTS idx_glossaries_owner_workid ON glossaries(owner_workid)",
]

LOCAL_TIME_MIGRATION_ID = "utc_timestamps_to_local_v1"


def _row_value(row, key: str, index: int):
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


async def _column_exists(db: aiosqlite.Connection, table: str, column: str) -> bool:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return any(_row_value(row, "name", 1) == column for row in rows)


async def _migration_applied(db: aiosqlite.Connection, migration_id: str) -> bool:
    cursor = await db.execute("SELECT 1 FROM schema_migrations WHERE id = ?", (migration_id,))
    return await cursor.fetchone() is not None


async def _convert_utc_column_to_local(db: aiosqlite.Connection, table: str, column: str) -> None:
    if not await _column_exists(db, table, column):
        return

    cursor = await db.execute(
        f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != ''"
    )
    rows = await cursor.fetchall()
    for row in rows:
        row_id = _row_value(row, "id", 0)
        original = _row_value(row, column, 1)
        converted = utc_timestamp_to_local_string(original)
        if converted and converted != original:
            await db.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (converted, row_id))


async def migrate_utc_timestamps_to_local(db: aiosqlite.Connection) -> bool:
    """Convert pre-local-time timestamp columns from UTC to app-local time once."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )"""
    )
    if await _migration_applied(db, LOCAL_TIME_MIGRATION_ID):
        return False

    for column in ("created_at", "started_at", "completed_at", "expires_at"):
        await _convert_utc_column_to_local(db, "translation_tasks", column)
    await _convert_utc_column_to_local(db, "glossaries", "created_at")
    await db.execute(
        "INSERT INTO schema_migrations (id, applied_at) VALUES (?, ?)",
        (LOCAL_TIME_MIGRATION_ID, local_now_string()),
    )
    return True


async def initialize_translation_statistics(db: aiosqlite.Connection) -> bool:
    """Seed counters from retained completed tasks when statistics are first introduced."""
    cursor = await db.execute("SELECT 1 FROM translation_statistics WHERE id = 1")
    if await cursor.fetchone():
        return False

    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM translation_tasks WHERE status = 'completed'"
    )
    total = (await cursor.fetchone())["cnt"]
    await db.execute(
        "INSERT INTO translation_statistics (id, completed_documents) VALUES (1, ?)",
        (total,),
    )

    cursor = await db.execute(
        """SELECT substr(completed_at, 1, 10) AS stat_date, COUNT(*) AS cnt
           FROM translation_tasks
           WHERE status = 'completed' AND completed_at IS NOT NULL AND completed_at != ''
           GROUP BY substr(completed_at, 1, 10)"""
    )
    for row in await cursor.fetchall():
        await db.execute(
            """INSERT INTO translation_daily_statistics (stat_date, completed_documents)
               VALUES (?, ?)""",
            (row["stat_date"], row["cnt"]),
        )
    return True


async def init_db():
    db = await get_db()
    try:
        await db.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                await db.execute(sql)
            except Exception:
                pass
        await migrate_utc_timestamps_to_local(db)
        await initialize_translation_statistics(db)
        await db.commit()

        from .services.builtin_glossary import seed_builtin_glossary
        await seed_builtin_glossary(db)
    finally:
        await db.close()
