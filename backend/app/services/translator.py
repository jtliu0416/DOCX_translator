"""LLM translation service with glossary injection and retry logic.

Supports two provider types:
- anthropic: Claude models via Anthropic SDK
- openai_compatible: DeepSeek, Qwen, GLM, Moonshot, Doubao, etc.
"""

import json
import asyncio
from typing import Optional

import anthropic
from openai import AsyncOpenAI

from ..config import (
    llm,
    TRANSLATION_BATCH_SIZE,
    LLM_MAX_RETRIES,
)
from ..database import get_db


async def get_glossary_terms(glossary_id: str) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT source_term, target_term, note FROM glossary_terms WHERE glossary_id = ?",
        (glossary_id,),
    )
    rows = await cursor.fetchall()
    await db.close()

    terms = [{"source": r["source_term"], "target": r["target_term"], "note": r["note"]} for r in rows]
    terms.sort(key=lambda t: len(t["source"]), reverse=True)
    return terms


def build_prompt(batch: list[dict], glossary_terms: Optional[list[dict]] = None) -> str:
    glossary_section = ""
    if glossary_terms:
        lines = [f'- "{t["source"]}" → "{t["target"]}"' for t in glossary_terms]
        glossary_section = (
            "\n## 术语表（必须严格遵守）\n"
            "以下是必须使用的术语翻译对照表，遇到相关词汇时必须使用指定译文：\n"
            + "\n".join(lines) + "\n"
        )

    items = [{"index": u["index"], "text": u["text"]} for u in batch]
    items_json = json.dumps(items, ensure_ascii=False)

    return f"""你是一个专业文档翻译专家。请将以下 JSON 数组中的每段文本从中文翻译为英文。
{glossary_section}
要求：
1. 保持原文的段落结构，一一对应
2. 遇到术语表中的词汇，必须使用指定译文
3. 专业术语需准确翻译
4. 只翻译中文内容，保留原文中的数字、公式、英文术语不变
5. 返回相同格式的 JSON 数组
6. 只返回翻译结果，不要添加解释

输入：
{items_json}

输出格式：
[{{"index": 0, "text": "..."}}]"""


def _parse_llm_response(raw: str, expected_count: int) -> list[dict]:
    raw = raw.strip()
    start = raw.find("[")
    end = raw.rfind("]") + 1
    if start >= 0 and end > start:
        raw = raw[start:end]

    translations = json.loads(raw)
    if len(translations) != expected_count:
        raise ValueError(
            f"Translation count mismatch: got {len(translations)}, expected {expected_count}"
        )
    return translations


async def _call_anthropic(prompt: str) -> str:
    client = anthropic.AsyncAnthropic(
        api_key=llm.api_key,
        base_url=llm.api_url if llm.api_url != "https://api.anthropic.com" else None,
    )
    message = await client.messages.create(
        model=llm.model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


async def _call_openai_compatible(prompt: str) -> str:
    client = AsyncOpenAI(
        api_key=llm.api_key,
        base_url=llm.api_url,
    )
    response = await client.chat.completions.create(
        model=llm.model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


async def translate_batch(
    batch: list[dict],
    glossary_terms: Optional[list[dict]] = None,
) -> list[dict]:
    prompt = build_prompt(batch, glossary_terms)
    call_fn = _call_anthropic if llm.provider == "anthropic" else _call_openai_compatible

    for attempt in range(LLM_MAX_RETRIES):
        try:
            raw = await call_fn(prompt)
            return _parse_llm_response(raw, len(batch))

        except (json.JSONDecodeError, ValueError):
            if attempt < LLM_MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
        except Exception as e:
            status = getattr(e, "status_code", None)
            if not status:
                resp = getattr(e, "response", None)
                if resp:
                    status = getattr(resp, "status_code", None)
            if status == 429 and attempt < LLM_MAX_RETRIES - 1:
                await asyncio.sleep(2 ** (attempt + 1))
                continue
            raise


async def _update_task_progress(task_id: str, status: str = None, progress: int = None,
                                 translated: int = None, total: int = None):
    """Helper to update task state with short-lived DB connections."""
    db = await get_db()
    parts, params = [], []
    if status:
        parts.append("status = ?")
        params.append(status)
    if progress is not None:
        parts.append("progress = ?")
        params.append(progress)
    if translated is not None:
        parts.append("translated_paragraphs = ?")
        params.append(translated)
    if total is not None:
        parts.append("total_paragraphs = ?")
        params.append(total)
    params.append(task_id)
    await db.execute(f"UPDATE translation_tasks SET {', '.join(parts)} WHERE id = ?", params)
    await db.commit()
    await db.close()


async def _translate_batch_and_track(
    batch: list[dict],
    glossary_terms: Optional[list[dict]],
    task_id: str,
    total: int,
    progress_lock: asyncio.Lock,
    done_count: list[int],
) -> list[dict]:
    """Translate a single batch and update shared progress."""
    translations = await translate_batch(batch, glossary_terms)

    async with progress_lock:
        done_count[0] += len(batch)
        progress = int(done_count[0] / total * 100)
        await _update_task_progress(task_id, progress=progress, translated=done_count[0])

    return translations


async def translate_all(
    units: list[dict],
    glossary_id: Optional[str] = None,
    task_id: str = "",
) -> list[dict]:
    to_translate = [u for u in units if not u.get("skip", False)]
    total = len(to_translate)

    if total == 0:
        return []

    glossary_terms = await get_glossary_terms(glossary_id) if glossary_id else None

    await _update_task_progress(task_id, status="translating", total=total)

    # Split into batches
    batches = [
        to_translate[i : i + TRANSLATION_BATCH_SIZE]
        for i in range(0, total, TRANSLATION_BATCH_SIZE)
    ]

    # Fire batches concurrently (max 3 to respect API rate limits)
    semaphore = asyncio.Semaphore(3)
    progress_lock = asyncio.Lock()
    done_count = [0]  # mutable counter shared across coroutines

    async def _limited(batch):
        async with semaphore:
            return await _translate_batch_and_track(batch, glossary_terms, task_id, total, progress_lock, done_count)

    results = await asyncio.gather(*[_limited(batch) for batch in batches])

    # Flatten results in order
    all_translations = []
    for batch_translations in results:
        all_translations.extend(batch_translations)

    return all_translations
