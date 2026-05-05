"""
Value Discovery Service — promoted from _quarantine/value_discovery.py (v0.8.0).
Detects VALUE, STALE, ARB, and NEUTRAL signals from odds data.
Changes from quarantine: start_polling() dropped (quarantine dep chain), get_signals() added,
singleton removed. Falls back to deterministic simulation when no live data is ingested.
"""
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from pydantic import BaseModel

logger = logging.getLogger("pbg.value_discovery")

_SIM_FIXTURES = [
    {"id": "LIV_ARS", "teams": "Liverpool vs Arsenal",       "market": "1X2_HOME",    "base": 2.10, "local": 2.35},
    {"id": "RMA_BAR", "teams": "Real Madrid vs Barcelona",   "market": "O2.5_GOALS",  "base": 1.85, "local": 1.95},
    {"id": "MCI_MUN", "teams": "Man City vs Man Utd",        "market": "BTTS_YES",    "base": 1.72, "local": 1.72},
    {"id": "PSG_BVB", "teams": "PSG vs Dortmund",            "market": "1X2_DRAW",    "base": 3.40, "local": 3.70},
    {"id": "JUV_INT", "teams": "Juventus vs Inter Milan",    "market": "1X2_AWAY",    "base": 2.60, "local": 2.65},
]


class MarketSignal(BaseModel):
    match_id: str
    teams: str
    market: str
    local_odds: float
    global_odds: float
    value_score: float
    signal_type: str
    timestamp: Optional[datetime] = None
    val_gap_score: float = 0.0  # (local_odds / global_fair_from_odds_api) - 1

    def model_post_init(self, __context) -> None:
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc))


class ValueDiscoveryService:
    def __init__(self):
        self.active_signals: List[MarketSignal] = []
        self._callbacks: List = []

    # ------------------------------------------------------------------
    # Core math — pure, testable
    # ------------------------------------------------------------------

    def calculate_value(self, local: float, global_baseline: float) -> Dict:
        if global_baseline <= 0:
            return {"score": 0.0, "type": "NEUTRAL"}
        implied_prob = 1 / global_baseline
        ev = round((local * implied_prob) - 1, 4)
        if ev > 0.05:
            return {"score": ev, "type": "VALUE"}
        if ev > 0:
            return {"score": ev, "type": "STALE"}
        return {"score": ev, "type": "NEUTRAL"}

    # ------------------------------------------------------------------
    # Signal emission (called by live ingestion or simulation)
    # ------------------------------------------------------------------

    def _emit(self, signal: MarketSignal) -> None:
        self.active_signals.insert(0, signal)
        self.active_signals = self.active_signals[:20]
        logger.info(
            "VDH_SIGNAL: %s | %s | edge=%.3f",
            signal.signal_type, signal.teams, signal.value_score,
        )
        for cb in self._callbacks:
            try:
                cb(signal)
            except Exception as exc:
                logger.warning("VDH callback error: %s", exc)

    def on_new_signal(self, callback) -> None:
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Live ingestion bridge (called by DataIngestionEngine if wired)
    # ------------------------------------------------------------------

    def process_ingested_odds(self, match_id: str, odds_list, teams: Optional[str] = None) -> None:
        by_market: Dict[str, Dict[str, List]] = defaultdict(lambda: defaultdict(list))
        for o in odds_list:
            by_market[o.market_type][o.selection].append((o.bookmaker, o.price))

        for market_type, selections in by_market.items():
            prices: Dict[str, Dict] = {}
            for sel, bm_prices in selections.items():
                best = max(p for _, p in bm_prices)
                avg = sum(p for _, p in bm_prices) / len(bm_prices)
                prices[sel] = {"best": best, "avg": avg}

            for sel, p in prices.items():
                analysis = self.calculate_value(p["best"], p["avg"])
                if analysis["type"] in ("VALUE", "STALE"):
                    self._emit(MarketSignal(
                        match_id=match_id,
                        teams=teams or match_id,
                        market=f"{market_type}_{sel.upper().replace(' ', '_')}",
                        local_odds=p["best"],
                        global_odds=round(p["avg"], 3),
                        value_score=analysis["score"],
                        signal_type=analysis["type"],
                        timestamp=datetime.now(timezone.utc),
                    ))

            if len(prices) >= 2:
                inv_sum = sum(1 / v["best"] for v in prices.values() if v["best"] > 0)
                if inv_sum < 1.0:
                    self._emit(MarketSignal(
                        match_id=match_id,
                        teams=teams or match_id,
                        market=f"ARB_{market_type}",
                        local_odds=round(1 / inv_sum, 3),
                        global_odds=1.0,
                        value_score=round(1 - inv_sum, 4),
                        signal_type="ARB",
                        timestamp=datetime.now(timezone.utc),
                    ))

    # ------------------------------------------------------------------
    # Public read — used by the API endpoint
    # ------------------------------------------------------------------

    def get_signals(self, limit: int = 20) -> List[MarketSignal]:
        if self.active_signals:
            return self.active_signals[:limit]
        return self._simulate(limit)

    def _simulate(self, limit: int) -> List[MarketSignal]:
        signals: List[MarketSignal] = []
        for f in _SIM_FIXTURES[:limit]:
            analysis = self.calculate_value(f["local"], f["base"])
            signals.append(MarketSignal(
                match_id=f["id"],
                teams=f["teams"],
                market=f["market"],
                local_odds=f["local"],
                global_odds=f["base"],
                value_score=analysis["score"],
                signal_type=analysis["type"],
                timestamp=datetime.now(timezone.utc),
            ))
        return signals
