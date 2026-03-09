"""Polymarket CLOB API client.

Wraps the public REST endpoints at https://clob.polymarket.com to fetch
market data used by the strategy engine.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CLOB_BASE_URL = "https://clob.polymarket.com"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    """A single binary outcome for a market."""

    name: str
    price: float  # probability in [0, 1]
    token_id: str = ""


@dataclass
class Market:
    """Represents a single Polymarket prediction market."""

    condition_id: str
    question: str
    outcomes: list[Outcome]
    volume: float
    end_date: datetime | None
    active: bool
    closed: bool
    slug: str = ""

    @property
    def yes_price(self) -> float | None:
        """Return the YES outcome price, if present."""
        for o in self.outcomes:
            if o.name.upper() == "YES":
                return o.price
        return self.outcomes[0].price if self.outcomes else None

    @property
    def no_price(self) -> float | None:
        """Return the NO outcome price, if present."""
        for o in self.outcomes:
            if o.name.upper() == "NO":
                return o.price
        return self.outcomes[1].price if len(self.outcomes) > 1 else None


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


class PolymarketClient:
    """Async HTTP client for the Polymarket CLOB and Gamma APIs.

    Args:
        timeout: Request timeout in seconds.
        max_retries: Number of retries on transient errors.
    """

    def __init__(self, timeout: float = 30.0, max_retries: int = 3) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "PolymarketClient":
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, base_url: str, path: str, params: dict[str, Any] | None = None) -> Any:
        """Perform a GET request with retry logic.

        Args:
            base_url: The API base URL.
            path: URL path to append.
            params: Optional query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            httpx.HTTPStatusError: On non-2xx response after retries.
            RuntimeError: If the client is not initialised.
        """
        if self._client is None:
            raise RuntimeError("Client not initialised — use as async context manager")

        url = f"{base_url}{path}"
        for attempt in range(self.max_retries):
            try:
                response = await self._client.get(url, params=params)
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                    logger.warning("Rate limited; sleeping %ds", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.TransportError as exc:
                if attempt == self.max_retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning("Transport error (%s); retrying in %ds", exc, wait)
                await asyncio.sleep(wait)

        raise RuntimeError(f"Failed to GET {url} after {self.max_retries} attempts")

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def get_markets(
        self,
        active: bool = True,
        limit: int = 100,
        next_cursor: str | None = None,
    ) -> tuple[list[Market], str | None]:
        """Fetch a page of markets from the Gamma API (better filtering).

        Args:
            active: If True, only return active (open) markets.
            limit: Page size (max 100).
            next_cursor: Pagination cursor (used as offset for Gamma API).

        Returns:
            A tuple of (markets, next_cursor). next_cursor is None when
            there are no more pages.
        """
        params: dict[str, Any] = {"limit": limit}
        if active:
            params["active"] = "true"
            params["closed"] = "false"
        if next_cursor:
            params["offset"] = next_cursor

        data = await self._get(GAMMA_BASE_URL, "/markets", params=params)

        # Gamma returns a flat list
        raw_markets: list[dict[str, Any]] = data if isinstance(data, list) else data.get("data", [])

        # Calculate next cursor (offset-based pagination)
        cursor: str | None = None
        if len(raw_markets) >= limit:
            current_offset = int(next_cursor) if next_cursor else 0
            cursor = str(current_offset + limit)

        markets: list[Market] = []
        for raw in raw_markets:
            market = _parse_market(raw)
            if active and (market.closed or not market.active):
                continue
            markets.append(market)

        return markets, cursor

    async def get_all_active_markets(self, max_pages: int = 10) -> list[Market]:
        """Fetch all active markets by paginating through results.

        Args:
            max_pages: Safety cap on number of pages to fetch.

        Returns:
            All active markets across all pages (up to max_pages).
        """
        all_markets: list[Market] = []
        cursor: str | None = None
        for _ in range(max_pages):
            markets, cursor = await self.get_markets(active=True, next_cursor=cursor)
            all_markets.extend(markets)
            if cursor is None:
                break
        return all_markets

    async def get_market(self, condition_id: str) -> Market | None:
        """Fetch a single market by condition ID.

        Args:
            condition_id: The market's condition ID.

        Returns:
            The parsed Market, or None if not found.
        """
        try:
            data = await self._get(CLOB_BASE_URL, f"/markets/{condition_id}")
            return _parse_market(data)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def get_market_snapshots(
        self, condition_ids: list[str]
    ) -> dict[str, Market]:
        """Fetch the latest state for a list of markets concurrently.

        Args:
            condition_ids: List of condition IDs to fetch.

        Returns:
            Mapping of condition_id -> Market.
        """
        tasks = [self.get_market(cid) for cid in condition_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        snapshots: dict[str, Market] = {}
        for cid, result in zip(condition_ids, results):
            if isinstance(result, Market):
                snapshots[cid] = result
            elif isinstance(result, Exception):
                logger.warning("Failed to fetch market %s: %s", cid, result)
        return snapshots


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_market(raw: dict[str, Any]) -> Market:
    """Parse a raw API dict into a Market dataclass.

    Args:
        raw: Raw JSON dict from the CLOB API.

    Returns:
        Populated Market instance.
    """
    # Parse outcomes — handle both CLOB (tokens list) and Gamma (outcomes + outcomePrices) formats
    outcomes: list[Outcome] = []
    tokens: list[dict[str, Any]] = raw.get("tokens", [])
    if tokens:
        # CLOB API format
        for token in tokens:
            outcomes.append(
                Outcome(
                    name=token.get("outcome", ""),
                    price=float(token.get("price", 0.0)),
                    token_id=token.get("token_id", ""),
                )
            )
    else:
        # Gamma API format: separate outcomes and outcomePrices arrays
        outcome_names = raw.get("outcomes", [])
        outcome_prices = raw.get("outcomePrices", [])
        clob_token_ids = raw.get("clobTokenIds", [])
        if isinstance(outcome_names, str):
            import json
            outcome_names = json.loads(outcome_names)
        if isinstance(outcome_prices, str):
            import json
            outcome_prices = json.loads(outcome_prices)
        if isinstance(clob_token_ids, str):
            import json
            clob_token_ids = json.loads(clob_token_ids)
        for i, name in enumerate(outcome_names):
            price = float(outcome_prices[i]) if i < len(outcome_prices) else 0.0
            token_id = clob_token_ids[i] if i < len(clob_token_ids) else ""
            outcomes.append(Outcome(name=name, price=price, token_id=token_id))

    end_date: datetime | None = None
    raw_end = raw.get("end_date_iso") or raw.get("endDateIso") or raw.get("end_date")
    if raw_end:
        try:
            # Ensure timezone-aware datetime (API returns UTC with trailing "Z")
            parsed = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                from datetime import timezone
                parsed = parsed.replace(tzinfo=timezone.utc)
            end_date = parsed
        except ValueError:
            pass

    volume_raw = raw.get("volume") or raw.get("volume_num") or 0.0

    return Market(
        condition_id=raw.get("condition_id", raw.get("conditionId", "")),
        question=raw.get("question", ""),
        outcomes=outcomes,
        volume=float(volume_raw),
        end_date=end_date,
        active=bool(raw.get("active", True)),
        closed=bool(raw.get("closed", False)),
        slug=raw.get("market_slug", raw.get("slug", "")),
    )
