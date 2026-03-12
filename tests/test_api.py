"""Tests for the Polymarket API client and parsing logic."""

from __future__ import annotations

import pytest

from polymarket_autopilot.api import _parse_market


class TestParseMarket:
    def _raw(self, **overrides: object) -> dict:
        base: dict = {
            "condition_id": "0xabc123",
            "question": "Will it rain tomorrow?",
            "tokens": [
                {"outcome": "YES", "price": "0.65", "token_id": "tok-yes"},
                {"outcome": "NO", "price": "0.35", "token_id": "tok-no"},
            ],
            "volume": "75000.0",
            "end_date_iso": "2025-12-31T00:00:00Z",
            "active": True,
            "closed": False,
            "market_slug": "will-it-rain",
        }
        base.update(overrides)
        return base

    def test_basic_parse(self) -> None:
        market = _parse_market(self._raw())
        assert market.condition_id == "0xabc123"
        assert market.question == "Will it rain tomorrow?"
        assert len(market.outcomes) == 2
        assert market.volume == 75000.0
        assert market.active is True
        assert market.closed is False
        assert market.slug == "will-it-rain"

    def test_yes_price(self) -> None:
        market = _parse_market(self._raw())
        assert abs((market.yes_price or 0) - 0.65) < 1e-6

    def test_no_price(self) -> None:
        market = _parse_market(self._raw())
        assert abs((market.no_price or 0) - 0.35) < 1e-6

    def test_end_date_parsed(self) -> None:
        market = _parse_market(self._raw())
        assert market.end_date is not None
        assert market.end_date.year == 2025

    def test_missing_end_date(self) -> None:
        market = _parse_market(self._raw(end_date_iso=None))
        assert market.end_date is None

    def test_empty_outcomes_rejected(self) -> None:
        with pytest.raises(ValueError, match="no valid outcomes"):
            _parse_market(self._raw(tokens=[]))

    def test_missing_volume_defaults_to_zero(self) -> None:
        raw = self._raw()
        del raw["volume"]
        market = _parse_market(raw)
        assert market.volume == 0.0


def test_missing_condition_id_rejected() -> None:
    with pytest.raises(ValueError, match="missing condition_id"):
        _parse_market({"question": "x", "tokens": [{"outcome": "YES", "price": 0.5}]})
