"""Glossary file parsing service (CSV, XLSX, TXT)."""

import csv
import os
from io import StringIO

import openpyxl

from ..database import get_db


def parse_glossary_file(file_path: str) -> list[dict]:
    """Parse a glossary file (CSV/XLSX/TXT) into terms list.

    Returns list of {source_term, target_term, note} dicts.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".xlsx":
        return _parse_xlsx(file_path)
    elif ext == ".csv":
        return _parse_csv(file_path)
    elif ext == ".txt":
        return _parse_txt(file_path)
    else:
        raise ValueError(f"Unsupported glossary format: {ext}")


def _parse_xlsx(path: str) -> list[dict]:
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    terms = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # skip header
        if row[0] and row[1]:
            terms.append({
                "source_term": str(row[0]).strip(),
                "target_term": str(row[1]).strip(),
                "note": str(row[2]).strip() if len(row) > 2 and row[2] else None,
            })
    wb.close()
    return terms


def _parse_csv(path: str) -> list[dict]:
    terms = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                continue  # skip header
            if len(row) >= 2 and row[0] and row[1]:
                terms.append({
                    "source_term": row[0].strip(),
                    "target_term": row[1].strip(),
                    "note": row[2].strip() if len(row) > 2 else None,
                })
    return terms


def _parse_txt(path: str) -> list[dict]:
    terms = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0] and parts[1]:
                terms.append({
                    "source_term": parts[0].strip(),
                    "target_term": parts[1].strip(),
                    "note": parts[2].strip() if len(parts) > 2 else None,
                })
    return terms


async def save_glossary_terms(glossary_id: str, terms: list[dict]):
    """Save parsed terms into the database."""
    db = await get_db()
    for t in terms:
        await db.execute(
            "INSERT INTO glossary_terms (glossary_id, source_term, target_term, note) VALUES (?, ?, ?, ?)",
            (glossary_id, t["source_term"], t["target_term"], t.get("note")),
        )
    await db.execute(
        "UPDATE glossaries SET term_count = ? WHERE id = ?",
        (len(terms), glossary_id),
    )
    await db.commit()
    await db.close()
