"""AI 只返回说明文本；评分和授信数值始终取专家引擎。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ReviewText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
CheckText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    position: int = Field(ge=0, le=2)
    code: str = Field(min_length=1, max_length=32)
    explanation: ReviewText
    checks: list[CheckText] = Field(min_length=1, max_length=3)


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[ReviewItem] = Field(min_length=1, max_length=3)


class AIReview(BaseModel):
    recordId: int
    status: Literal["not_requested", "pending", "ready", "failed", "disabled", "not_applicable"]
    attemptId: int = 0
    version: int = 0
    items: list[dict] = Field(default_factory=list)
    preview: list[dict] = Field(default_factory=list)
    model: str | None = None
    promptVersion: str | None = None
    generatedAt: str | None = None
    errorCode: str | None = None
    message: str | None = None
    retryAfter: int = 0
    retryable: bool = False
