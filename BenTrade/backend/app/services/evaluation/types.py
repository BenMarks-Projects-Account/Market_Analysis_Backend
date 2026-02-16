from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvaluationContext:
    rules: dict[str, Any] = field(default_factory=dict)
    validation_mode: bool = False


@dataclass
class EvaluationResult:
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    composite_score: float | None = None
