from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class TaskCreate(BaseModel):
    task_id: str
    status: str = "pending"


class TaskResponse(BaseModel):
    task_id: str
    original_filename: str
    source_lang: str
    target_lang: str
    status: str
    progress: int
    total_paragraphs: int = 0
    translated_paragraphs: int = 0
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class TaskDetail(BaseModel):
    task_id: str
    status: str
    progress: int
    total_paragraphs: int = 0
    translated_paragraphs: int = 0
    error_message: Optional[str] = None
