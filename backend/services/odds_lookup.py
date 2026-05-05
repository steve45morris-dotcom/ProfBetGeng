"""
OddsLookupService — fetches global fair odds from The Odds API and enriches
NormalizedSelection.val_gap_score = (local_odds / global_fair) - 1.

TTL-cached: one API call every 30 minutes covers all upcoming matches.
Falls back to val_gap_score=0.0 per selection if match not found.
"""
import asyncio
import logging
import re
import time
from typing import Dict, List, Optional

import httpx

from ..models import NormalizedSelection

logger = logging.getLogger(__name__)

_CACHE_TTL = 1800  # 30 minutes
_SPORTS = "soccer"
_BASE_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"


def _normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _token_overlap(a: str, b: str) -> float:
    """Fraction of tokens in `a` that appear in `b`."""
    ta = set(_normalize_name(a).split())
    tb = set(_normalize_name(b).split())
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


def _fuzzy_match(query: str, candidates: List[str], threshold: float = 0.5) -> Optional[str]:
    """Return the best candidate if overlap score >= threshold, else None."""
    best_score = 0.0
    best = None
    for c in candidates:
        score = _token_overlap(query, c)
        if score > best_score:
            best_score = score
            best = c
    return best if best_score >= threshold else None


class OddsLookupService:
    """
    Thin async wrapper around The Odds API with an in-process TTL cache.
    Thread-safe for asyncio contexts (single-threaded event loop).
    """

    def __init__(self, api_key: str):
        self._api_key = api_key
        # cache: event_name -> {"home_1x2": float, "draw_1x2": float, "away_1x2": float}
        self._cache: Dict[str, Dict[str, float]] = {}
        self._cache_at: float = 0.0
        self._lock = asyncio.Lock()

    async def _load(self) -> None:
        """Fetch all upcoming soccer odds and populate cache."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    _BASE_URL.format(sport=_SPORTS),
                    params={
                        "apiKey": self._api_key,
                        "regions": "eu,uk",
                        "markets": "h2h",
                        "oddsFormat": "decimal",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("[OddsLookup] Failed to fetch odds: %s", exc)
            return

        new_cache: Dict[str, Dict[str, float]] = {}
        for match in data:
            home = match.get("home_team", "")
            away = match.get("away_team", "")
            key = f"{home} vs {away}"

            # Aggregate h2h prices across bookmakers — take the mean
            home_prices, draw_prices, away_prices = [], [], []
            for bm in match.get("bookmakers", []):
                for market in bm.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                    if home in outcomes:
                        home_prices.append(outcomes[home])
                    if away in outcomes:
                        away_prices.append(outcomes[away])
                    if "Draw" in outcomes:
                        draw_prices.append(outcomes["Draw"])

            entry: Dict[str, float] = {}
            if home_prices:
                entry["home"] = sum(home_prices) / len(home_prices)
            if draw_prices:
                entry["draw"] = sum(draw_prices) / len(draw_prices)
            if away_prices:
                entry["away"] = sum(away_prices) / len(away_prices)

            if entry:
                new_cache[key] = entry

        self._cache = new_cache
        self._cache_at = time.monotonic()
        logger.info("[OddsLookup] Cache refreshed — %d matches loaded.", len(new_cache))

    async def _ensure_fresh(self) -> None:
        async with self._lock:
            if time.monotonic() - self._cache_at > _CACHE_TTL:
                await self._load()

    def _lookup_fair(self, event_name: str, pick: str) -> Optional[float]:
        """Return the market-consensus fair price for this selection, or None."""
        candidates = list(self._cache.keys())
        best_match = _fuzzy_match(event_name, candidates)
        if best_match is None:
            return None

        entry = self._cache[best_match]
        pick_lower = _normalize_name(pick)

        # Map pick labels to home/draw/away
        if pick_lower in ("1", "home", "home win"):
            return entry.get("home")
        if pick_lower in ("x", "draw", "tie"):
            return entry.get("draw")
        if pick_lower in ("2", "away", "away win"):
            return entry.get("away")

        return None

    async def enrich_val_gap(self, selections: List[NormalizedSelection]) -> None:
        """
        Mutates val_gap_score on each selection in-place.
        val_gap_score = (local_odds / global_fair) - 1
        Stays 0.0 if match/market not found in cache.
        """
        if not self._api_key:
            return
        await self._ensure_fresh()

        for sel in selections:
            fair = self._lookup_fair(sel.event_name, sel.pick)
            if fair and fair > 0:
                sel.val_gap_score = round((sel.odds / fair) - 1.0, 4)

    @staticmethod
    def _market_to_pick(market: str) -> Optional[str]:
        """Extract 1X2 pick from market strings like '1X2_HOME', '1X2_DRAW', '1X2_AWAY'."""
        m = market.upper()
        if "HOME" in m:
            return "home"
        if "DRAW" in m:
            return "draw"
        if "AWAY" in m:
            return "away"
        return None

    async def enrich_market_signals(self, signals) -> None:
        """
        Mutates val_gap_score on each MarketSignal in-place.
        Only enriches 1X2 markets (HOME/DRAW/AWAY) — skips BTTS, O2.5, ARB, etc.
        Stays 0.0 if match not found in cache or API key absent.
        """
        if not self._api_key:
            return
        await self._ensure_fresh()

        for signal in signals:
            pick = self._market_to_pick(signal.market)
            if pick is None:
                continue
            fair = self._lookup_fair(signal.teams, pick)
            if fair and fair > 0:
                signal.val_gap_score = round((signal.local_odds / fair) - 1.0, 4)


class MockOddsLookupService:
    """Test double — returns fixed val_gap_score stubs."""

    async def enrich_val_gap(self, selections: List[NormalizedSelection]) -> None:
        for sel in selections:
            sel.val_gap_score = 0.05  # stub: 5% edge above fair

    async def enrich_market_signals(self, signals) -> None:
        for signal in signals:
            m = signal.market.upper()
            if any(x in m for x in ("HOME", "DRAW", "AWAY")):
                signal.val_gap_score = 0.05  # stub: 5% edge


_odds_service: Optional[OddsLookupService] = None


def get_odds_lookup_service() -> Optional[OddsLookupService]:
    """DI getter — singleton, returns None if THE_ODDS_API_KEY is not set."""
    global _odds_service
    if _odds_service is None:
        from ..config import get_settings
        settings = get_settings()
        if settings.the_odds_api_key:
            _odds_service = OddsLookupService(api_key=settings.the_odds_api_key)
    return _odds_service
