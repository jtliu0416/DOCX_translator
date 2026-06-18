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


LANGUAGE_NAMES = {
    "zh": "中文",
    "en": "英文",
}


def _normalize_lang(lang: str | None) -> str:
    value = (lang or "").strip().lower()
    if value in ("zh", "zh-cn", "zh_cn", "chinese", "中文"):
        return "zh"
    if value in ("en", "en-us", "en_us", "english"):
        return "en"
    return value or "zh"


def _language_name(lang: str | None) -> str:
    normalized = _normalize_lang(lang)
    return LANGUAGE_NAMES.get(normalized, normalized)


async def get_glossary_terms(
    glossary_id: str,
    source_lang: str = "zh",
    target_lang: str = "en",
) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT g.source_lang, g.target_lang, t.source_term, t.target_term, t.note
           FROM glossary_terms t
           JOIN glossaries g ON g.id = t.glossary_id
           WHERE t.glossary_id = ?""",
        (glossary_id,),
    )
    rows = await cursor.fetchall()
    await db.close()

    requested_source = _normalize_lang(source_lang)
    requested_target = _normalize_lang(target_lang)
    terms = []
    for r in rows:
        glossary_source = _normalize_lang(r["source_lang"])
        glossary_target = _normalize_lang(r["target_lang"])
        if glossary_source == requested_target and glossary_target == requested_source:
            source, target = r["target_term"], r["source_term"]
        else:
            source, target = r["source_term"], r["target_term"]

        terms.append({"source": source, "target": target, "note": r["note"]})

    terms.sort(key=lambda t: len(t["source"]), reverse=True)
    return terms


def build_prompt(
    batch: list[dict],
    glossary_terms: Optional[list[dict]] = None,
    source_lang: str = "zh",
    target_lang: str = "en",
) -> str:
    source_name = _language_name(source_lang)
    target_name = _language_name(target_lang)
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

    return f"""你是一个专业文档翻译专家。请将以下 JSON 数组中的每段文本从{source_name}翻译为{target_name}。
{glossary_section}
要求：
1. 保持原文的段落结构，一一对应
2. 遇到术语表中的词汇，必须使用指定译文
3. 专业术语需准确翻译
4. 只翻译{source_name}内容，保留原文中的数字、公式、代码、编号和非{source_name}内容不变
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
    source_lang: str = "zh",
    target_lang: str = "en",
) -> list[dict]:
    prompt = build_prompt(batch, glossary_terms, source_lang, target_lang)
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

    # Broadcast via WebSocket
    from ..api.ws import manager as ws_manager
    await ws_manager.broadcast(task_id, {
        "task_id": task_id,
        "status": status,
        "progress": progress,
        "translated_paragraphs": translated,
        "total_paragraphs": total,
    })


async def _translate_batch_and_track(
    batch: list[dict],
    glossary_terms: Optional[list[dict]],
    task_id: str,
    total: int,
    progress_lock: asyncio.Lock,
    done_count: list[int],
    source_lang: str,
    target_lang: str,
) -> list[dict]:
    """Translate a single batch and update shared progress."""
    translations = await translate_batch(batch, glossary_terms, source_lang, target_lang)

    async with progress_lock:
        done_count[0] += len(batch)
        progress = int(done_count[0] / total * 100)
        await _update_task_progress(task_id, progress=progress, translated=done_count[0])

    return translations


async def _merge_glossary_terms(
    glossary_id: Optional[str],
    use_builtin: bool,
    source_lang: str,
    target_lang: str,
) -> list[dict]:
    """Merge built-in and user glossary terms, with user terms taking priority."""
    builtin_terms = []
    user_terms = []
    source = _normalize_lang(source_lang)
    target = _normalize_lang(target_lang)

    if use_builtin and {source, target} == {"zh", "en"}:
        builtin_terms = await get_glossary_terms(
            "builtin-biopharma-zh-en",
            source_lang=source,
            target_lang=target,
        )

    if glossary_id:
        user_terms = await get_glossary_terms(
            glossary_id,
            source_lang=source,
            target_lang=target,
        )

    if not builtin_terms:
        return user_terms
    if not user_terms:
        return builtin_terms

    # Merge: user terms override built-in on conflict (by source term)
    user_sources = {t["source"].lower(): t for t in user_terms}
    merged = list(user_terms)
    for bt in builtin_terms:
        if bt["source"].lower() not in user_sources:
            merged.append(bt)
    merged.sort(key=lambda t: len(t["source"]), reverse=True)
    return merged


async def translate_all(
    units: list[dict],
    glossary_id: Optional[str] = None,
    task_id: str = "",
    use_builtin: bool = False,
    source_lang: str = "zh",
    target_lang: str = "en",
) -> list[dict]:
    to_translate = [u for u in units if not u.get("skip", False)]
    total = len(to_translate)

    if total == 0:
        return []

    glossary_terms = None
    if glossary_id or use_builtin:
        glossary_terms = await _merge_glossary_terms(
            glossary_id,
            use_builtin,
            source_lang,
            target_lang,
        )

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
            return await _translate_batch_and_track(
                batch,
                glossary_terms,
                task_id,
                total,
                progress_lock,
                done_count,
                source_lang,
                target_lang,
            )

    results = await asyncio.gather(*[_limited(batch) for batch in batches])

    # Flatten results in order
    all_translations = []
    for batch_translations in results:
        all_translations.extend(batch_translations)

    return all_translations
