"""LLM translation service with glossary injection and retry logic.

Supports two provider types:
- anthropic: Claude models via Anthropic SDK
- openai_compatible: DeepSeek, Qwen, GLM, Moonshot, Doubao, etc.
"""

import asyncio
import json
import logging
import os
import re
from functools import lru_cache
from typing import Optional

import anthropic
import tiktoken
from openai import AsyncOpenAI

from ..config import (
    llm,
    LLM_DISABLE_REASONING,
    LLM_OPENAI_COMPATIBLE_EXTRA_BODY_JSON,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_MAX_RETRIES,
    TOKENIZER_ENCODING,
    TRANSLATION_BATCH_CONCURRENCY,
    TRANSLATION_BATCH_INPUT_TOKEN_LIMIT,
    TRANSLATION_BATCH_MAX_UNITS,
    UPLOAD_DIR,
)
from ..database import get_db
from ..time_utils import local_now_string


logger = logging.getLogger("uvicorn.error")

LANGUAGE_NAMES = {
    "zh": "中文",
    "en": "英文",
}

DEFAULT_TOKENIZER_ENCODING = "cl100k_base"


class LLMRequestTracker:
    def __init__(self) -> None:
        self.request_count = 0
        self._lock = asyncio.Lock()

    async def next_request_number(self) -> int:
        async with self._lock:
            self.request_count += 1
            return self.request_count


class TranslationResponseMismatch(ValueError):
    def __init__(
        self,
        *,
        actual_count: int,
        expected_count: int,
        expected_indices: list,
        returned_indices: list,
        missing_indices: list,
        unexpected_indices: list,
        duplicate_indices: list,
        partial_translations: list[dict],
    ) -> None:
        details = [
            f"Translation count mismatch: got {actual_count}, expected {expected_count}",
        ]
        if missing_indices:
            details.append(f"missing_indices={missing_indices}")
        if unexpected_indices:
            details.append(f"unexpected_indices={unexpected_indices}")
        if duplicate_indices:
            details.append(f"duplicate_indices={duplicate_indices}")
        super().__init__("; ".join(details))
        self.actual_count = actual_count
        self.expected_count = expected_count
        self.expected_indices = expected_indices
        self.returned_indices = returned_indices
        self.missing_indices = missing_indices
        self.unexpected_indices = unexpected_indices
        self.duplicate_indices = duplicate_indices
        self.partial_translations = partial_translations


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


@lru_cache(maxsize=1)
def get_tokenizer():
    try:
        return tiktoken.get_encoding(TOKENIZER_ENCODING)
    except Exception:
        logger.warning(
            "Tokenizer encoding %s is unavailable; falling back to %s",
            TOKENIZER_ENCODING,
            DEFAULT_TOKENIZER_ENCODING,
        )
        return tiktoken.get_encoding(DEFAULT_TOKENIZER_ENCODING)


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(get_tokenizer().encode(text, disallowed_special=()))


def _normalize_match_text(text: str) -> str:
    return text.casefold()


def _split_glossary_note_aliases(note: str | None) -> list[str]:
    if not note:
        return []
    return [
        part.strip()
        for part in re.split(r"[,;/，；、\n\r]+", note)
        if part.strip()
    ]


def _term_matches_text(term: str, text: str, normalized_text: str) -> bool:
    term = term.strip()
    if not term:
        return False

    if any("\u4e00" <= char <= "\u9fff" for char in term):
        return term in text

    escaped = re.escape(term)
    pattern = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _glossary_match_terms(term: dict) -> list[str]:
    candidates = [term.get("source", "")]
    candidates.extend(_split_glossary_note_aliases(term.get("note")))
    return [candidate.strip() for candidate in candidates if candidate and candidate.strip()]


def _filter_glossary_terms_for_batch(
    batch: list[dict],
    glossary_terms: Optional[list[dict]],
) -> list[dict]:
    if not glossary_terms:
        return []

    text = "\n".join(str(item.get("text", "")) for item in batch)
    normalized_text = _normalize_match_text(text)
    matched = []
    seen_sources = set()

    for term in glossary_terms:
        source = term.get("source", "").strip()
        if not source:
            continue
        if source.casefold() in seen_sources:
            continue

        match_terms = _glossary_match_terms(term)
        if any(_term_matches_text(match_term, text, normalized_text) for match_term in match_terms):
            matched.append(term)
            seen_sources.add(source.casefold())

    matched.sort(
        key=lambda item: (
            item.get("_priority", 1),
            -max((len(match_term) for match_term in _glossary_match_terms(item)), default=0),
            item.get("source", "").casefold(),
        )
    )

    return matched


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
4. 如果文本来自 Excel 单元格、sheet 页签名或 PPT 页面文本，该文件是单语言文件，不要因为夹杂英文缩写、单位、批号、编号、方法名或产品代码而判断为双语内容
5. 人名也必须处理：中文人名翻译为拼音形式（姓在前、名在后，首字母大写，例如“张三”译为“Zhang San”），英文人名翻译为常见中文音译；不要原样保留源语言人名
6. 只翻译{source_name}内容，保留原文中的数字、公式、代码、编号、单位、批号、专业英文缩写和非{source_name}内容不变
7. 返回 JSON 对象，顶层必须包含 translations 数组；每项必须使用输入中的原始 index，不要重新编号、不要补充输入中不存在的 index
8. 只返回翻译结果，不要添加解释

输入：
{items_json}

输出格式：
{{"translations":[{{"index": 123, "text": "..."}}]}}"""


def _prompt_for_batch(
    batch: list[dict],
    glossary_terms: Optional[list[dict]],
    source_lang: str,
    target_lang: str,
) -> tuple[str, list[dict]]:
    matched_glossary_terms = _filter_glossary_terms_for_batch(batch, glossary_terms)
    prompt = build_prompt(batch, matched_glossary_terms, source_lang, target_lang)
    return prompt, matched_glossary_terms


def _prompt_token_count_for_batch(
    batch: list[dict],
    glossary_terms: Optional[list[dict]],
    source_lang: str,
    target_lang: str,
) -> tuple[int, int]:
    prompt, matched_glossary_terms = _prompt_for_batch(
        batch,
        glossary_terms,
        source_lang,
        target_lang,
    )
    return count_tokens(prompt), len(matched_glossary_terms)


def _unit_location(unit: dict) -> str:
    details = [f"index={unit.get('index')}"]
    unit_type = unit.get("type")
    if unit_type:
        details.append(f"type={unit_type}")
    if unit.get("sheet_name"):
        details.append(f"sheet={unit.get('sheet_name')}")
    if unit.get("coordinate"):
        details.append(f"cell={unit.get('coordinate')}")
    if unit.get("table_index") is not None:
        details.append(
            "table={table}, row={row}, col={col}".format(
                table=unit.get("table_index"),
                row=unit.get("row_index"),
                col=unit.get("col_index"),
            )
        )
    if unit.get("slide_index") is not None:
        details.append(f"slide={unit.get('slide_index')}")
    return ", ".join(details)


def _raise_oversized_unit(unit: dict, token_count: int) -> None:
    raise ValueError(
        "单个翻译片段超过批次 token 上限，无法在不拆分段落/单元格的情况下翻译："
        f"{_unit_location(unit)}, prompt_tokens={token_count}, "
        f"limit={TRANSLATION_BATCH_INPUT_TOKEN_LIMIT}。"
        "请拆分源文档中的对应段落或单元格后重试。"
    )


def build_token_limited_batches(
    units: list[dict],
    glossary_terms: Optional[list[dict]],
    source_lang: str = "zh",
    target_lang: str = "en",
) -> list[list[dict]]:
    """Build ordered batches whose full prompts stay within the input token limit."""
    batches: list[list[dict]] = []
    start = 0
    total = len(units)

    while start < total:
        first_unit = units[start]
        first_tokens, _ = _prompt_token_count_for_batch(
            [first_unit],
            glossary_terms,
            source_lang,
            target_lang,
        )
        if first_tokens > TRANSLATION_BATCH_INPUT_TOKEN_LIMIT:
            _raise_oversized_unit(first_unit, first_tokens)

        low = start + 1
        high = min(total, start + TRANSLATION_BATCH_MAX_UNITS)
        best = start + 1

        while low <= high:
            mid = (low + high) // 2
            candidate = units[start:mid]
            candidate_tokens, _ = _prompt_token_count_for_batch(
                candidate,
                glossary_terms,
                source_lang,
                target_lang,
            )
            if candidate_tokens <= TRANSLATION_BATCH_INPUT_TOKEN_LIMIT:
                best = mid
                low = mid + 1
            else:
                high = mid - 1

        batches.append(units[start:best])
        start = best

    return batches


def _extract_llm_translations(raw: str | None) -> list[dict]:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("LLM returned empty content")

    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        object_start = raw.find("{")
        array_start = raw.find("[")
        starts = [pos for pos in (object_start, array_start) if pos >= 0]
        if not starts:
            raise

        start = min(starts)
        closing = "}" if raw[start] == "{" else "]"
        end = raw.rfind(closing) + 1
        if end <= start:
            raise
        payload = json.loads(raw[start:end])

    if isinstance(payload, dict):
        translations = payload.get("translations")
    else:
        translations = payload

    if not isinstance(translations, list):
        raise ValueError("LLM response must contain a translations array")
    return translations


def _parse_llm_response(raw: str | None, expected: int | list) -> list[dict]:
    translations = _extract_llm_translations(raw)

    if isinstance(expected, int):
        if len(translations) != expected:
            raise TranslationResponseMismatch(
                actual_count=len(translations),
                expected_count=expected,
                expected_indices=[],
                returned_indices=[],
                missing_indices=[],
                unexpected_indices=[],
                duplicate_indices=[],
                partial_translations=[],
            )
        return translations

    expected_indices = list(expected)
    expected_index_set = set(expected_indices)
    returned_indices = []
    duplicate_indices = []
    translations_by_index = {}

    for item in translations:
        if not isinstance(item, dict):
            raise ValueError("LLM translations array must contain objects")
        index = item.get("index")
        returned_indices.append(index)
        if index in translations_by_index:
            duplicate_indices.append(index)
            continue
        translations_by_index[index] = item

    missing_indices = [
        index for index in expected_indices
        if index not in translations_by_index
    ]
    unexpected_indices = [
        index for index in returned_indices
        if index not in expected_index_set
    ]
    partial_translations = [
        translations_by_index[index]
        for index in expected_indices
        if index in translations_by_index
    ]

    if (
        len(translations) != len(expected_indices)
        or missing_indices
        or unexpected_indices
        or duplicate_indices
    ):
        raise TranslationResponseMismatch(
            actual_count=len(translations),
            expected_count=len(expected_indices),
            expected_indices=expected_indices,
            returned_indices=returned_indices,
            missing_indices=missing_indices,
            unexpected_indices=unexpected_indices,
            duplicate_indices=duplicate_indices,
            partial_translations=partial_translations,
        )

    return [translations_by_index[index] for index in expected_indices]


def _serialize_llm_response(response: object) -> str:
    if response is None:
        return "None"

    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        try:
            return json.dumps(
                model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        except TypeError:
            try:
                return json.dumps(
                    model_dump(),
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            except Exception:
                pass

    to_dict = getattr(response, "dict", None)
    if callable(to_dict):
        try:
            return json.dumps(to_dict(), ensure_ascii=False, indent=2, default=str)
        except Exception:
            pass

    return repr(response)


def _deep_merge_dicts(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _openai_compatible_extra_body() -> dict | None:
    extra_body: dict = {}

    if LLM_DISABLE_REASONING:
        extra_body = {
            # DashScope/Qwen OpenAI-compatible APIs use this top-level flag.
            "enable_thinking": False,
            # vLLM forwards these values into model chat templates.
            "chat_template_kwargs": {
                "enable_thinking": False,
                "thinking": False,
            },
        }

    if LLM_OPENAI_COMPATIBLE_EXTRA_BODY_JSON.strip():
        try:
            custom_extra_body = json.loads(LLM_OPENAI_COMPATIBLE_EXTRA_BODY_JSON)
        except json.JSONDecodeError as exc:
            raise ValueError("LLM_OPENAI_COMPATIBLE_EXTRA_BODY_JSON must be a JSON object") from exc
        if not isinstance(custom_extra_body, dict):
            raise ValueError("LLM_OPENAI_COMPATIBLE_EXTRA_BODY_JSON must be a JSON object")
        extra_body = _deep_merge_dicts(extra_body, custom_extra_body)

    return extra_body or None


def _log_llm_response(
    raw: str | None,
    full_response: str,
    batch: list[dict],
    attempt: int,
    task_id: str = "",
) -> None:
    indices = [item.get("index") for item in batch]
    logger.info(
        "LLM response received task_id=%s provider=%s model=%s attempt=%s "
        "batch_size=%s indices=%s response_chars=%s content_chars=%s",
        task_id,
        llm.provider,
        llm.model,
        attempt + 1,
        len(batch),
        indices,
        len(full_response),
        len(raw) if raw else 0,
    )


def _log_matched_glossary_terms(
    matched_terms: list[dict],
    batch: list[dict],
    task_id: str = "",
) -> None:
    indices = [item.get("index") for item in batch]
    logger.info(
        "Matched glossary terms task_id=%s batch_size=%s indices=%s count=%s",
        task_id,
        len(batch),
        indices,
        len(matched_terms),
    )


def _batch_attempt_label(value: int | None, fallback: str) -> str:
    return f"{value:03d}" if isinstance(value, int) and value > 0 else fallback


def _write_llm_failure_artifact(
    *,
    task_id: str,
    batch: list[dict],
    prompt: str,
    raw: str | None,
    full_response: str,
    error: Exception,
    attempt: int,
    batch_number: int | None,
    total_batches: int | None,
    prompt_tokens: int,
    matched_glossary_terms: list[dict],
    task_request_number: int | None,
) -> str | None:
    if not task_id:
        return None

    failure_dir = os.path.join(UPLOAD_DIR, task_id, "llm_failures")
    try:
        os.makedirs(failure_dir, exist_ok=True)
        batch_label = _batch_attempt_label(batch_number, "unknown")
        attempt_label = _batch_attempt_label(attempt + 1, "unknown")
        path = os.path.join(failure_dir, f"batch_{batch_label}_attempt_{attempt_label}.json")
        if os.path.exists(path):
            request_label = _batch_attempt_label(task_request_number, "unknown")
            path = os.path.join(
                failure_dir,
                f"batch_{batch_label}_attempt_{attempt_label}_request_{request_label}.json",
            )
        translations_count = None
        returned_indices = None
        if isinstance(raw, str) and raw.strip():
            try:
                translations = _extract_llm_translations(raw)
                translations_count = len(translations)
                returned_indices = [
                    item.get("index") if isinstance(item, dict) else None
                    for item in translations
                ]
            except Exception:
                translations_count = None
                returned_indices = None

        missing_indices = getattr(error, "missing_indices", None)
        unexpected_indices = getattr(error, "unexpected_indices", None)
        duplicate_indices = getattr(error, "duplicate_indices", None)

        artifact = {
            "created_at": local_now_string(),
            "task_id": task_id,
            "provider": llm.provider,
            "model": llm.model,
            "batch_no": batch_number,
            "total_batches": total_batches,
            "attempt": attempt + 1,
            "task_request_no": task_request_number,
            "batch_size": len(batch),
            "expected_translations": len(batch),
            "actual_translations": translations_count,
            "indices": [item.get("index") for item in batch],
            "returned_indices": returned_indices,
            "missing_indices": missing_indices,
            "unexpected_indices": unexpected_indices,
            "duplicate_indices": duplicate_indices,
            "prompt_tokens": prompt_tokens,
            "input_token_limit": TRANSLATION_BATCH_INPUT_TOKEN_LIMIT,
            "max_output_tokens": LLM_MAX_OUTPUT_TOKENS,
            "matched_glossary_terms": [
                {
                    "source": term.get("source"),
                    "target": term.get("target"),
                    "note": term.get("note"),
                }
                for term in matched_glossary_terms
            ],
            "error_type": type(error).__name__,
            "error_message": str(error),
            "prompt": prompt,
            "raw_content": raw,
            "full_response": full_response,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(artifact, f, ensure_ascii=False, indent=2)
        return path
    except Exception as artifact_error:
        logger.warning(
            "Failed to write LLM failure artifact task_id=%s batch_no=%s attempt=%s: %s",
            task_id,
            batch_number,
            attempt + 1,
            artifact_error,
        )
        return None


async def _call_anthropic(prompt: str) -> tuple[str | None, str]:
    client = anthropic.AsyncAnthropic(
        api_key=llm.api_key,
        base_url=llm.api_url if llm.api_url != "https://api.anthropic.com" else None,
    )
    message = await client.messages.create(
        model=llm.model,
        max_tokens=LLM_MAX_OUTPUT_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    content = message.content[0].text if message.content else None
    return content, _serialize_llm_response(message)


async def _call_openai_compatible(prompt: str) -> tuple[str | None, str]:
    client = AsyncOpenAI(
        api_key=llm.api_key,
        base_url=llm.api_url,
    )
    request_kwargs = {
        "model": llm.model,
        "max_tokens": LLM_MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }
    extra_body = _openai_compatible_extra_body()
    if extra_body:
        request_kwargs["extra_body"] = extra_body
    response = await client.chat.completions.create(**request_kwargs)
    content = response.choices[0].message.content if response.choices else None
    return content, _serialize_llm_response(response)


async def translate_batch(
    batch: list[dict],
    glossary_terms: Optional[list[dict]] = None,
    source_lang: str = "zh",
    target_lang: str = "en",
    task_id: str = "",
    batch_number: int | None = None,
    total_batches: int | None = None,
    request_tracker: Optional[LLMRequestTracker] = None,
) -> list[dict]:
    prompt, matched_glossary_terms = _prompt_for_batch(
        batch,
        glossary_terms,
        source_lang,
        target_lang,
    )
    indices = [item.get("index") for item in batch]
    prompt_tokens = count_tokens(prompt)
    _log_matched_glossary_terms(matched_glossary_terms, batch, task_id)
    logger.info(
        "LLM prompt tokens task_id=%s batch_no=%s total_batches=%s batch_size=%s "
        "indices=%s prompt_tokens=%s limit=%s matched_glossary_terms=%s",
        task_id,
        batch_number,
        total_batches,
        len(batch),
        indices,
        prompt_tokens,
        TRANSLATION_BATCH_INPUT_TOKEN_LIMIT,
        len(matched_glossary_terms),
    )
    call_fn = _call_anthropic if llm.provider == "anthropic" else _call_openai_compatible

    for attempt in range(LLM_MAX_RETRIES):
        raw = None
        full_response = ""
        task_request_number = None
        try:
            if request_tracker is not None:
                task_request_number = await request_tracker.next_request_number()
            logger.info(
                "LLM request task_id=%s provider=%s model=%s task_request_no=%s "
                "batch_no=%s total_batches=%s batch_request_no=%s batch_size=%s "
                "indices=%s prompt_tokens=%s max_output_tokens=%s input_token_limit=%s "
                "matched_glossary_terms=%s",
                task_id,
                llm.provider,
                llm.model,
                task_request_number,
                batch_number,
                total_batches,
                attempt + 1,
                len(batch),
                indices,
                prompt_tokens,
                LLM_MAX_OUTPUT_TOKENS,
                TRANSLATION_BATCH_INPUT_TOKEN_LIMIT,
                len(matched_glossary_terms),
            )
            raw, full_response = await call_fn(prompt)
            _log_llm_response(raw, full_response, batch, attempt, task_id)
            return _parse_llm_response(raw, indices)

        except (json.JSONDecodeError, ValueError) as e:
            artifact_path = _write_llm_failure_artifact(
                task_id=task_id,
                batch=batch,
                prompt=prompt,
                raw=raw,
                full_response=full_response,
                error=e,
                attempt=attempt,
                batch_number=batch_number,
                total_batches=total_batches,
                prompt_tokens=prompt_tokens,
                matched_glossary_terms=matched_glossary_terms,
                task_request_number=task_request_number,
            )
            if artifact_path:
                logger.info(
                    "LLM failure artifact task_id=%s batch_no=%s attempt=%s path=%s",
                    task_id,
                    batch_number,
                    attempt + 1,
                    artifact_path,
                )
            if attempt < LLM_MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            if isinstance(e, TranslationResponseMismatch):
                missing_index_set = set(e.missing_indices)
                missing_batch = [
                    item for item in batch
                    if item.get("index") in missing_index_set
                ]
                if e.partial_translations and missing_batch and len(missing_batch) < len(batch):
                    logger.info(
                        "LLM partial recovery task_id=%s batch_no=%s total_batches=%s "
                        "missing_count=%s missing_indices=%s partial_count=%s",
                        task_id,
                        batch_number,
                        total_batches,
                        len(missing_batch),
                        e.missing_indices,
                        len(e.partial_translations),
                    )
                    recovered_translations = await translate_batch(
                        missing_batch,
                        glossary_terms,
                        source_lang,
                        target_lang,
                        task_id,
                        batch_number,
                        total_batches,
                        request_tracker,
                    )
                    combined_by_index = {
                        item.get("index"): item
                        for item in e.partial_translations
                    }
                    for item in recovered_translations:
                        combined_by_index[item.get("index")] = item

                    remaining_missing = [
                        index for index in indices
                        if index not in combined_by_index
                    ]
                    if not remaining_missing:
                        logger.info(
                            "LLM partial recovery succeeded task_id=%s batch_no=%s "
                            "recovered_count=%s total_batch_size=%s",
                            task_id,
                            batch_number,
                            len(recovered_translations),
                            len(batch),
                        )
                        return [combined_by_index[index] for index in indices]
                    logger.warning(
                        "LLM partial recovery incomplete task_id=%s batch_no=%s "
                        "remaining_missing_indices=%s",
                        task_id,
                        batch_number,
                        remaining_missing,
                    )
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
    batch_number: int | None = None,
    total_batches: int | None = None,
    request_tracker: Optional[LLMRequestTracker] = None,
) -> list[dict]:
    """Translate a single batch and update shared progress."""
    translations = await translate_batch(
        batch,
        glossary_terms,
        source_lang,
        target_lang,
        task_id,
        batch_number,
        total_batches,
        request_tracker,
    )

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
        for term in builtin_terms:
            term["_priority"] = 1

    if glossary_id:
        user_terms = await get_glossary_terms(
            glossary_id,
            source_lang=source,
            target_lang=target,
        )
        for term in user_terms:
            term["_priority"] = 0

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
    merged.sort(key=lambda t: (t.get("_priority", 1), -len(t["source"])))
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

    request_tracker = LLMRequestTracker()
    batches: list[list[dict]] = []

    try:
        batches = build_token_limited_batches(
            to_translate,
            glossary_terms,
            source_lang,
            target_lang,
        )
        logger.info(
            "LLM task batching task_id=%s provider=%s model=%s total_units=%s "
            "total_batches=%s input_token_limit=%s max_batch_units=%s",
            task_id,
            llm.provider,
            llm.model,
            total,
            len(batches),
            TRANSLATION_BATCH_INPUT_TOKEN_LIMIT,
            TRANSLATION_BATCH_MAX_UNITS,
        )

        semaphore = asyncio.Semaphore(TRANSLATION_BATCH_CONCURRENCY)
        progress_lock = asyncio.Lock()
        done_count = [0]  # mutable counter shared across coroutines

        async def _limited(batch_index: int, batch: list[dict]):
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
                    batch_index + 1,
                    len(batches),
                    request_tracker,
                )

        tasks = [
            asyncio.create_task(_limited(batch_index, batch))
            for batch_index, batch in enumerate(batches)
        ]
        try:
            results = await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    except Exception as exc:
        logger.info(
            "LLM task request summary task_id=%s provider=%s model=%s outcome=failed "
            "request_count=%s total_batches=%s total_units=%s error=%s",
            task_id,
            llm.provider,
            llm.model,
            request_tracker.request_count,
            len(batches),
            total,
            exc,
        )
        raise
    else:
        logger.info(
            "LLM task request summary task_id=%s provider=%s model=%s outcome=completed "
            "request_count=%s total_batches=%s total_units=%s",
            task_id,
            llm.provider,
            llm.model,
            request_tracker.request_count,
            len(batches),
            total,
        )

    # Flatten results in order
    all_translations = []
    for batch_translations in results:
        all_translations.extend(batch_translations)

    return all_translations
