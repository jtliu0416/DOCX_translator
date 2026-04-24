import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load .env file from backend directory
load_dotenv(os.path.join(BASE_DIR, ".env"))

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
RESULT_DIR = os.path.join(BASE_DIR, "results")
GLOSSARY_DIR = os.path.join(BASE_DIR, "glossaries")

MAX_FILE_SIZE = 10 * 1024 * 1024       # 10MB
MAX_PARALLEL_TASKS = 50
MAX_CONCURRENT_TRANSLATIONS = 2
TRANSLATION_BATCH_SIZE = 20
LLM_MAX_RETRIES = 3
TASK_EXPIRE_DAYS = 7
TOKEN_EXPIRE_DAYS = 30


class LLMSingleton:
    provider: str = os.getenv("LLM_PROVIDER", "anthropic")
    api_url: str = os.getenv("LLM_API_URL", "https://api.anthropic.com")
    api_key: str = os.getenv("LLM_API_KEY", "")
    model: str = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

llm = LLMSingleton()

DATABASE_PATH = os.path.join(BASE_DIR, "doctrans.db")

SUPPORTED_LANGUAGES = [
    {"code": "zh", "name": "中文"},
    {"code": "en", "name": "English"},
]
