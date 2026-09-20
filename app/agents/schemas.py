"""Typed contracts between agents. Every agent output is validated against one of these; a violation triggers the
runtime's repair loop instead of silently passing garbage downstream."""
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    field: str
    value: Any = None
    why: str = ""


class Finding(BaseModel):
    direction: Literal["suspicious", "benign", "mixed"]
    strength: int = Field(ge=1, le=5)
    summary: str
    evidence: list[Evidence] = []


class Challenge(BaseModel):
    plausibility: Literal["low", "medium", "high"]  # how plausible is the benign explanation
    arguments: list[Evidence] = []
    missing_evidence: list[str] = []


class Verdict(BaseModel):
    summary: str
    score_adjustment: int = 0
    adjustment_reason: str = ""
    key_indicators: list[dict] = []
    mitigating_factors: list[dict] = []
    recommended_action: str = "ROUTINE_REVIEW"
    next_steps: list[str] = []
    open_questions: list[str] = []
    self_confidence: float = Field(ge=0, le=1)
    contested: bool = False
    contested_reason: str = ""


class PanelVote(BaseModel):
    score_adjustment: int = 0
    recommended_action: str = "ROUTINE_REVIEW"
    confidence: float = Field(ge=0, le=1)
    rationale: str = ""


class FastItem(BaseModel):
    case_id: str
    summary: str
    recommended_action: Literal["CLEAR_FALSE_POSITIVE", "ROUTINE_REVIEW"] = "ROUTINE_REVIEW"
    score_adjustment: int = 0
    mitigating_factors: list[dict] = []
    open_questions: list[str] = []


class FastBatch(BaseModel):
    results: list[FastItem]
