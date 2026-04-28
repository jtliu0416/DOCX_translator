"""Built-in biopharma glossary seed data and initialization."""

from .builtin_terms_data import BUILTIN_TERMS

BUILTIN_GLOSSARY_ID = "builtin-biopharma-zh-en"
BUILTIN_GLOSSARY_NAME = "生物制药专业术语对照表"


async def seed_builtin_glossary(db):
    """Insert built-in glossary if not already present."""
    await db.execute(
        """INSERT OR IGNORE INTO glossaries (id, token, name, source_lang, target_lang, file_path, term_count, is_builtin)
        VALUES (?, '__builtin__', ?, 'zh', 'en', '__builtin__', ?, 1)""",
        (BUILTIN_GLOSSARY_ID, BUILTIN_GLOSSARY_NAME, len(BUILTIN_TERMS)),
    )
    cursor = await db.execute("SELECT changes()")
    if (await cursor.fetchone())[0] == 0:
        return

    await db.executemany(
        "INSERT INTO glossary_terms (glossary_id, source_term, target_term, note) VALUES (?, ?, ?, ?)",
        [(BUILTIN_GLOSSARY_ID, source, target, note) for source, target, note in BUILTIN_TERMS],
    )

    await db.commit()
