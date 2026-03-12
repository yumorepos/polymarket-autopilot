"""Tests for the Polymarket API client and parsing logic."""

from __future__ import annotations

import asyncio

from polymarket_autopilot.api import Market, Outcome, PolymarketClient, _parse_market


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

    def test_empty_outcomes(self) -> None:
        market = _parse_market(self._raw(tokens=[]))
        assert market.outcomes == []
        assert market.yes_price is None
        assert market.no_price is None

    def test_missing_volume_defaults_to_zero(self) -> None:
        raw = self._raw()
        del raw["volume"]
        market = _parse_market(raw)
        assert market.volume == 0.0


class TestClientFetchStats:
    def test_get_all_active_markets_with_stats(self) -> None:
        payload = [
            {
                "condition_id": "c1",
                "question": "Q1",
                "outcomes": '["YES", "NO"]',
                "outcomePrices": "[0.6, 0.4]",
                "active": True,
                "closed": False,
                "volume": 100,
            },
            {
                "condition_id": "c2",
                "question": "Q2",
                "outcomes": '["YES", "NO"]',
                "outcomePrices": "[0.2, 0.8]",
                "active": False,
                "closed": True,
                "volume": 50,
            },
        ]

        client = PolymarketClient()

        async def fake_get(base_url: str, path: str, params: dict | None = None) -> list[dict]:
            return payload

        client._get = fake_get  # type: ignore[method-assign]

        markets, stats = asyncio.run(client.get_all_active_markets_with_stats(max_pages=1))
        assert len(markets) == 1
        assert markets[0].condition_id == "c1"
        assert stats.raw_markets_seen == 2
        assert stats.parsed_markets == 2
        assert stats.active_markets == 1
        assert stats.filtered_inactive == 1
