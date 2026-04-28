"""Built-in biopharma glossary seed data and initialization."""

from .builtin_terms_data import BUILTIN_TERMS

BUILTIN_GLOSSARY_ID = "builtin-biopharma-zh-en"
BUILTIN_GLOSSARY_NAME = "生物制药专业术语对照表"


async def seed_builtin_glossary(db):
    """Insert built-in glossary if not already present."""
    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM glossaries WHERE is_builtin = 1"
    )
    row = await cursor.fetchone()
    if row["cnt"] > 0:
        return

    await db.execute(
        """INSERT INTO glossaries (id, token, name, source_lang, target_lang, file_path, term_count, is_builtin)
        VALUES (?, '__builtin__', ?, 'zh', 'en', '__builtin__', ?, 1)""",
        (BUILTIN_GLOSSARY_ID, BUILTIN_GLOSSARY_NAME, len(BUILTIN_TERMS)),
    )

    for source, target, note in BUILTIN_TERMS:
        await db.execute(
            "INSERT INTO glossary_terms (glossary_id, source_term, target_term, note) VALUES (?, ?, ?, ?)",
            (BUILTIN_GLOSSARY_ID, source, target, note),
        )

    await db.commit()
