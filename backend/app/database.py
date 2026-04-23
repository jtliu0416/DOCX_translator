import aiosqlite
from .config import DATABASE_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS translation_tasks (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
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
    completed_at TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS glossaries (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
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

"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    return db


MIGRATIONS: list[str] = []


async def init_db():
    db = await get_db()
    await db.executescript(SCHEMA)
    await db.commit()
    await db.close()
