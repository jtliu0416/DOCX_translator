import json
import sys
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import translator  # noqa: E402


def _unit(index: int, text: str, **extra):
    return {"index": index, "type": "paragraph", "text": text, **extra}


def _prompt_tokens(units, glossary_terms=None):
    matched = translator._filter_glossary_terms_for_batch(units, glossary_terms)
    return translator.count_tokens(translator.build_prompt(units, matched, "zh", "en"))


class TokenLimitedBatchingTest(TestCase):
    def setUp(self) -> None:
        self._old_limit = translator.TRANSLATION_BATCH_INPUT_TOKEN_LIMIT
        self._old_max_units = translator.TRANSLATION_BATCH_MAX_UNITS
        self._old_encoding = translator.TOKENIZER_ENCODING

    def tearDown(self) -> None:
        translator.TRANSLATION_BATCH_INPUT_TOKEN_LIMIT = self._old_limit
        translator.TRANSLATION_BATCH_MAX_UNITS = self._old_max_units
        translator.TOKENIZER_ENCODING = self._old_encoding
        translator.get_tokenizer.cache_clear()

    def test_invalid_tokenizer_encoding_falls_back(self) -> None:
        translator.TOKENIZER_ENCODING = "not-a-real-tokenizer"
        translator.get_tokenizer.cache_clear()

        self.assertGreater(translator.count_tokens("主细胞库测试"), 0)

    def test_many_short_units_can_exceed_legacy_batch_size(self) -> None:
        translator.TRANSLATION_BATCH_INPUT_TOKEN_LIMIT = 10000
        translator.TRANSLATION_BATCH_MAX_UNITS = 300
        units = [_unit(i, f"短文本{i}") for i in range(25)]

        batches = translator.build_token_limited_batches(units, None, "zh", "en")

        self.assertEqual([len(batch) for batch in batches], [25])
        self.assertEqual([item["index"] for batch in batches for item in batch], list(range(25)))
        self.assertLessEqual(_prompt_tokens(batches[0]), translator.TRANSLATION_BATCH_INPUT_TOKEN_LIMIT)

    def test_long_unit_reduces_batch_size_without_splitting_unit(self) -> None:
        units = [
            _unit(0, "短段落"),
            _unit(1, "长段落包含主细胞库、宿主细胞蛋白和内毒素。" * 120),
            _unit(2, "另一个短段落"),
        ]
        single_long_tokens = _prompt_tokens([units[1]])
        combined_tokens = _prompt_tokens(units[:2])
        self.assertGreater(combined_tokens, single_long_tokens)
        translator.TRANSLATION_BATCH_INPUT_TOKEN_LIMIT = single_long_tokens
        translator.TRANSLATION_BATCH_MAX_UNITS = 300

        batches = translator.build_token_limited_batches(units, None, "zh", "en")

        self.assertEqual([[item["index"] for item in batch] for batch in batches], [[0], [1], [2]])

    def test_glossary_injection_can_force_new_batch(self) -> None:
        glossary_terms = [
            {
                "source": "主细胞库",
                "target": "Master Cell Bank with deliberately long controlled term wording " * 20,
                "note": "MCB",
            }
        ]
        first = _unit(0, "普通短段落")
        second = _unit(1, "本段包含主细胞库。")
        single_first_tokens = _prompt_tokens([first], glossary_terms)
        single_second_tokens = _prompt_tokens([second], glossary_terms)
        combined_tokens = _prompt_tokens([first, second], glossary_terms)
        translator.TRANSLATION_BATCH_INPUT_TOKEN_LIMIT = max(single_first_tokens, single_second_tokens)
        translator.TRANSLATION_BATCH_MAX_UNITS = 300
        self.assertGreater(combined_tokens, translator.TRANSLATION_BATCH_INPUT_TOKEN_LIMIT)

        batches = translator.build_token_limited_batches(
            [first, second],
            glossary_terms,
            "zh",
            "en",
        )

        self.assertEqual([[item["index"] for item in batch] for batch in batches], [[0], [1]])

    def test_single_unit_over_limit_fails_clearly(self) -> None:
        unit = _unit(7, "超长段落内容" * 300, table_index=1, row_index=2, col_index=3)
        single_tokens = _prompt_tokens([unit])
        translator.TRANSLATION_BATCH_INPUT_TOKEN_LIMIT = single_tokens - 1
        translator.TRANSLATION_BATCH_MAX_UNITS = 300

        with self.assertRaises(ValueError) as context:
            translator.build_token_limited_batches([unit], None, "zh", "en")

        message = str(context.exception)
        self.assertIn("index=7", message)
        self.assertIn("prompt_tokens=", message)
        self.assertIn(f"limit={translator.TRANSLATION_BATCH_INPUT_TOKEN_LIMIT}", message)


class OpenAICompatibleExtraBodyTest(TestCase):
    def setUp(self) -> None:
        self._old_disable_reasoning = translator.LLM_DISABLE_REASONING
        self._old_extra_body_json = translator.LLM_OPENAI_COMPATIBLE_EXTRA_BODY_JSON

    def tearDown(self) -> None:
        translator.LLM_DISABLE_REASONING = self._old_disable_reasoning
        translator.LLM_OPENAI_COMPATIBLE_EXTRA_BODY_JSON = self._old_extra_body_json

    def test_disable_reasoning_adds_common_thinking_flags(self) -> None:
        translator.LLM_DISABLE_REASONING = True
        translator.LLM_OPENAI_COMPATIBLE_EXTRA_BODY_JSON = ""

        extra_body = translator._openai_compatible_extra_body()

        self.assertEqual(
            extra_body,
            {
                "enable_thinking": False,
                "chat_template_kwargs": {
                    "enable_thinking": False,
                    "thinking": False,
                },
            },
        )

    def test_custom_extra_body_json_merges_over_defaults(self) -> None:
        translator.LLM_DISABLE_REASONING = True
        translator.LLM_OPENAI_COMPATIBLE_EXTRA_BODY_JSON = (
            '{"chat_template_kwargs":{"reasoning_effort":"none"},"temperature":0}'
        )

        extra_body = translator._openai_compatible_extra_body()

        self.assertEqual(extra_body["enable_thinking"], False)
        self.assertEqual(extra_body["temperature"], 0)
        self.assertEqual(
            extra_body["chat_template_kwargs"],
            {
                "enable_thinking": False,
                "thinking": False,
                "reasoning_effort": "none",
            },
        )


class LLMRequestLoggingTest(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._old_provider = translator.llm.provider
        self._old_model = translator.llm.model
        self._old_max_retries = translator.LLM_MAX_RETRIES
        self._old_call_openai_compatible = translator._call_openai_compatible
        self._old_upload_dir = translator.UPLOAD_DIR
        self._temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self) -> None:
        translator.llm.provider = self._old_provider
        translator.llm.model = self._old_model
        translator.LLM_MAX_RETRIES = self._old_max_retries
        translator._call_openai_compatible = self._old_call_openai_compatible
        translator.UPLOAD_DIR = self._old_upload_dir
        self._temp_dir.cleanup()

    async def test_each_model_request_is_counted_and_logs_prompt_tokens(self) -> None:
        calls = []

        async def fake_call_openai_compatible(prompt: str):
            calls.append(prompt)
            if len(calls) == 1:
                return '{"translations":[]}', "first response"
            return '{"translations":[{"index":0,"text":"Translated"}]}', "second response"

        translator.llm.provider = "openai_compatible"
        translator.llm.model = "unit-test-model"
        translator.LLM_MAX_RETRIES = 2
        translator._call_openai_compatible = fake_call_openai_compatible
        tracker = translator.LLMRequestTracker()

        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            result = await translator.translate_batch(
                [_unit(0, "测试")],
                task_id="task-1",
                batch_number=1,
                total_batches=1,
                request_tracker=tracker,
            )

        self.assertEqual(result, [{"index": 0, "text": "Translated"}])
        self.assertEqual(len(calls), 2)
        self.assertEqual(tracker.request_count, 2)
        logs = "\n".join(captured.output)
        self.assertIn("LLM request task_id=task-1", logs)
        self.assertIn("task_request_no=1", logs)
        self.assertIn("task_request_no=2", logs)
        self.assertIn("batch_request_no=1", logs)
        self.assertIn("batch_request_no=2", logs)
        self.assertRegex(logs, r"prompt_tokens=\d+")

    async def test_mismatch_writes_llm_failure_artifact(self) -> None:
        async def fake_call_openai_compatible(prompt: str):
            return '{"translations":[{"index":0,"text":"Only one"}]}', "full response"

        translator.llm.provider = "openai_compatible"
        translator.llm.model = "unit-test-model"
        translator.LLM_MAX_RETRIES = 1
        translator._call_openai_compatible = fake_call_openai_compatible
        translator.UPLOAD_DIR = self._temp_dir.name

        with self.assertRaises(ValueError):
            await translator.translate_batch(
                [_unit(0, "第一段"), _unit(1, "第二段")],
                task_id="task-artifact",
                batch_number=2,
                total_batches=3,
                request_tracker=translator.LLMRequestTracker(),
            )

        artifact_path = (
            Path(self._temp_dir.name)
            / "task-artifact"
            / "llm_failures"
            / "batch_002_attempt_001.json"
        )
        self.assertTrue(artifact_path.exists())

        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(artifact["task_id"], "task-artifact")
        self.assertEqual(artifact["batch_no"], 2)
        self.assertEqual(artifact["attempt"], 1)
        self.assertEqual(artifact["expected_translations"], 2)
        self.assertEqual(artifact["actual_translations"], 1)
        self.assertIn("Translation count mismatch", artifact["error_message"])
        self.assertIn("Only one", artifact["raw_content"])
        self.assertIn("第一段", artifact["prompt"])
        self.assertEqual(artifact["missing_indices"], [1])

    async def test_missing_translation_is_recovered_with_targeted_retry(self) -> None:
        calls = []

        async def fake_call_openai_compatible(prompt: str):
            calls.append(prompt)
            if len(calls) == 1:
                return (
                    '{"translations":['
                    '{"index":0,"text":"First"},'
                    '{"index":2,"text":"Third"}'
                    ']}',
                    "partial response",
                )
            return '{"translations":[{"index":1,"text":"Second"}]}', "missing response"

        translator.llm.provider = "openai_compatible"
        translator.llm.model = "unit-test-model"
        translator.LLM_MAX_RETRIES = 1
        translator._call_openai_compatible = fake_call_openai_compatible
        translator.UPLOAD_DIR = self._temp_dir.name
        tracker = translator.LLMRequestTracker()

        result = await translator.translate_batch(
            [_unit(0, "第一段"), _unit(1, "第二段"), _unit(2, "第三段")],
            task_id="task-recover",
            batch_number=1,
            total_batches=1,
            request_tracker=tracker,
        )

        self.assertEqual([item["index"] for item in result], [0, 1, 2])
        self.assertEqual([item["text"] for item in result], ["First", "Second", "Third"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(tracker.request_count, 2)
        self.assertIn('"index": 1, "text": "第二段"', calls[1])
        self.assertNotIn("第一段", calls[1])
        self.assertNotIn("第三段", calls[1])

    async def test_equal_count_wrong_index_is_recovered(self) -> None:
        calls = []

        async def fake_call_openai_compatible(prompt: str):
            calls.append(prompt)
            if len(calls) == 1:
                return (
                    '{"translations":['
                    '{"index":0,"text":"First"},'
                    '{"index":99,"text":"Unexpected"}'
                    ']}',
                    "wrong-index response",
                )
            return '{"translations":[{"index":1,"text":"Second"}]}', "missing response"

        translator.llm.provider = "openai_compatible"
        translator.llm.model = "unit-test-model"
        translator.LLM_MAX_RETRIES = 1
        translator._call_openai_compatible = fake_call_openai_compatible
        tracker = translator.LLMRequestTracker()

        result = await translator.translate_batch(
            [_unit(0, "第一段"), _unit(1, "第二段")],
            task_id="task-wrong-index",
            batch_number=1,
            total_batches=1,
            request_tracker=tracker,
        )

        self.assertEqual(result, [{"index": 0, "text": "First"}, {"index": 1, "text": "Second"}])
        self.assertEqual(len(calls), 2)
