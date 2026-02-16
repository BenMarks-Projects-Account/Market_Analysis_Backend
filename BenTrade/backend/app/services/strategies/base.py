from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StrategyPlugin(ABC):
    id: str = "base"
    display_name: str = "Base Strategy"

    @abstractmethod
    def build_candidates(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def enrich(self, candidates: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def evaluate(self, trade: dict[str, Any]) -> tuple[bool, list[str]]:
        raise NotImplementedError

    @abstractmethod
    def score(self, trade: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        raise NotImplementedError
