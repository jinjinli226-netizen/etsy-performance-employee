from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class KnowledgeStatus(StrEnum):
    PROPOSED = "proposed"
    TESTING = "testing"
    ACTIVE = "active"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class KnowledgeCandidateCreate(BaseModel):
    title: str
    proposal: dict[str, Any]


class KnowledgeCandidateRead(KnowledgeCandidateCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: KnowledgeStatus
    created_at: datetime
    updated_at: datetime


class RuleVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: str
    rules: dict[str, Any]
    status: KnowledgeStatus
    created_at: datetime
