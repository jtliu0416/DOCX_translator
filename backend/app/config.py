import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load .env file from backend directory
load_dotenv(os.path.join(BASE_DIR, ".env"))

UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
RESULT_DIR = os.getenv("RESULT_DIR", os.path.join(BASE_DIR, "results"))
GLOSSARY_DIR = os.getenv("GLOSSARY_DIR", os.path.join(BASE_DIR, "glossaries"))
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Shanghai")
APP_VERSION = os.getenv("APP_VERSION", "dev")

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ISSUER = os.getenv("JWT_ISSUER", "")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "")
JWT_LEEWAY_SECONDS = int(os.getenv("JWT_LEEWAY_SECONDS", "60"))
if not 0 <= JWT_LEEWAY_SECONDS <= 300:
    raise ValueError("JWT_LEEWAY_SECONDS must be between 0 and 300")
CORS_ALLOWED_ORIGINS = tuple(
    origin.strip().rstrip("/")
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
)

def _optional_positive_int_env(name: str, default: str = "0") -> int | None:
    value = int(os.getenv(name, default))
    return value if value > 0 else None

MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "100"))
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024 if MAX_FILE_SIZE_MB > 0 else None
MAX_FILE_SIZE_LABEL = f"{MAX_FILE_SIZE_MB}MB" if MAX_FILE_SIZE_MB > 0 else "不限"
UPLOAD_CHUNK_SIZE_MB = int(os.getenv("UPLOAD_CHUNK_SIZE_MB", "5"))
UPLOAD_CHUNK_SIZE = max(1, UPLOAD_CHUNK_SIZE_MB) * 1024 * 1024
UPLOAD_CHUNK_SIZE_LABEL = f"{max(1, UPLOAD_CHUNK_SIZE_MB)}MB"
MAX_PARALLEL_TASKS = _optional_positive_int_env("MAX_PARALLEL_TASKS", "20")
MAX_CONCURRENT_TRANSLATIONS = _optional_positive_int_env("MAX_CONCURRENT_TRANSLATIONS", "2")
TRANSLATION_BATCH_SIZE = 20
TRANSLATION_BATCH_CONCURRENCY = max(1, int(os.getenv("TRANSLATION_BATCH_CONCURRENCY", "3")))
TRANSLATION_BATCH_INPUT_TOKEN_LIMIT = max(1, int(os.getenv("TRANSLATION_BATCH_INPUT_TOKEN_LIMIT", "5000")))
TRANSLATION_BATCH_MAX_UNITS = max(1, int(os.getenv("TRANSLATION_BATCH_MAX_UNITS", "300")))
TOKENIZER_ENCODING = os.getenv("TOKENIZER_ENCODING", "cl100k_base")
LLM_MAX_OUTPUT_TOKENS = max(1, int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "4096")))
LLM_MAX_RETRIES = 3
TASK_EXPIRE_DAYS = 7
TOKEN_EXPIRE_DAYS = 30
LLM_DISABLE_REASONING = os.getenv("LLM_DISABLE_REASONING", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}
LLM_OPENAI_COMPATIBLE_EXTRA_BODY_JSON = os.getenv("LLM_OPENAI_COMPATIBLE_EXTRA_BODY_JSON", "")


class LLMSingleton:
    provider: str = os.getenv("LLM_PROVIDER", "anthropic")
    api_url: str = os.getenv("LLM_API_URL", "https://api.anthropic.com")
    api_key: str = os.getenv("LLM_API_KEY", "")
    model: str = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

llm = LLMSingleton()

DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "doctrans.db"))

SUPPORTED_LANGUAGES = [
    {"code": "zh", "name": "中文"},
    {"code": "en", "name": "English"},
]
