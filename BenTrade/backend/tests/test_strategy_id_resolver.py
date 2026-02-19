"""Tests for strategy_id_resolver — the single canonical entry-point."""

from unittest.mock import patch

import pytest

from app.utils.strategy_id_resolver import (
    StrategyResolutionError,
    resolve_strategy_id,
    resolve_strategy_id_or_none,
    _STRATEGY_ALIASES,
)
from app.utils.trade_key import CANONICAL_STRATEGY_IDS


# ── canonical pass-through ───────────────────────────────────────────


@pytest.mark.parametrize("sid", sorted(CANONICAL_STRATEGY_IDS))
def test_canonical_ids_pass_through(sid: str) -> None:
    """Every canonical strategy_id must resolve to itself with no event."""
    with patch(
        "app.utils.strategy_id_resolver._emit_alias_event"
    ) as mock_emit:
        result = resolve_strategy_id(sid)
    assert result == sid
    mock_emit.assert_not_called()


# ── alias mapping with WARN event ───────────────────────────────────

_KNOWN_ALIASES = {k: v for k, v in _STRATEGY_ALIASES.items() if k != v}


@pytest.mark.parametrize("alias,expected", sorted(_KNOWN_ALIASES.items()))
def test_alias_mapping_emits_event(alias: str, expected: str) -> None:
    """Known aliases resolve correctly and emit STRATEGY_ALIAS_USED."""
    with patch(
        "app.utils.strategy_id_resolver._emit_alias_event"
    ) as mock_emit:
        result = resolve_strategy_id(alias)
    assert result == expected
    mock_emit.assert_called_once_with(alias, expected)


def test_alias_mapping_emit_event_false() -> None:
    """When emit_event=False, no validation event should fire."""
    with patch(
        "app.utils.strategy_id_resolver._emit_alias_event"
    ) as mock_emit:
        result = resolve_strategy_id("credit_put_spread", emit_event=False)
    assert result == "put_credit_spread"
    mock_emit.assert_not_called()


# ── unknown / empty → StrategyResolutionError ────────────────────────


@pytest.mark.parametrize("bad_value", ["", None, "   ", "bogus_strategy", "x"])
def test_unknown_raises_strategy_resolution_error(bad_value: str) -> None:
    with pytest.raises(StrategyResolutionError) as exc_info:
        resolve_strategy_id(bad_value)
    err = exc_info.value
    assert hasattr(err, "provided")
    assert "Valid strategy IDs" in str(err)


def test_error_is_value_error_subclass() -> None:
    """StrategyResolutionError must subclass ValueError for clean 400s."""
    with pytest.raises(ValueError):
        resolve_strategy_id("not_a_strategy")


# ── resolve_strategy_id_or_none ──────────────────────────────────────


def test_or_none_returns_none_for_unknown() -> None:
    assert resolve_strategy_id_or_none("nope") is None
    assert resolve_strategy_id_or_none("") is None
    assert resolve_strategy_id_or_none(None) is None


def test_or_none_returns_canonical() -> None:
    assert resolve_strategy_id_or_none("put_credit_spread") == "put_credit_spread"


def test_or_none_resolves_alias() -> None:
    assert resolve_strategy_id_or_none("credit_put_spread") == "put_credit_spread"


# ── case insensitivity ───────────────────────────────────────────────


def test_case_insensitive() -> None:
    assert resolve_strategy_id("PUT_CREDIT_SPREAD") == "put_credit_spread"
    assert resolve_strategy_id("Iron_Condor") == "iron_condor"


# ── alias target integrity ──────────────────────────────────────────


def test_all_alias_targets_are_canonical() -> None:
    """Every value in _STRATEGY_ALIASES must be in CANONICAL_STRATEGY_IDS."""
    for alias, target in _STRATEGY_ALIASES.items():
        assert target in CANONICAL_STRATEGY_IDS, (
            f"alias '{alias}' -> '{target}' is not canonical"
        )
