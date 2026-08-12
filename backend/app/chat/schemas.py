from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageCreate(BaseModel):
    role: MessageRole
    content: str


class MessageRead(MessageCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    created_at: datetime


class ConversationCreate(BaseModel):
    title: str


class ConversationRead(ConversationCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    messages: list[MessageRead]
