from pydantic import BaseModel
from typing import Optional, List


class GlossaryCreate(BaseModel):
    glossary_id: str
    name: str
    term_count: int


class GlossaryResponse(BaseModel):
    id: str
    name: str
    source_lang: str
    target_lang: str
    term_count: int
    created_at: Optional[str] = None


class GlossaryDetail(GlossaryResponse):
    terms: List[dict] = []
