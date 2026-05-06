"""
Portfolio Service — M8-b (v1.0.1).
Aggregates bankroll, CLV, risk, and alpha into a single portfolio snapshot.
No external API deps — pure aggregation of existing services.
"""
import logging
from datetime import datetime, timezone
from typing import List, Protocol

from pydantic import BaseModel

from .bankroll_service import BankrollService, KellyRecommendation
from .clv_service import CLVService, CLVReport
from .risk_analytics_service import RiskAnalyticsService, PortfolioRiskMetrics
from .alpha_service import AlphaService, PricingFrame

logger = logging.getLogger("pbg.portfolio_service")


class AlphaSummary(BaseModel):
    top_signals: List[PricingFrame]
    signal_count: int
    avg_edge: float


class PortfolioSummary(BaseModel):
    bankroll: KellyRecommendation
    clv: CLVReport
    risk: PortfolioRiskMetrics
    alpha: AlphaSummary
    generated_at: str


class PortfolioServiceProtocol(Protocol):
    def get_portfolio(
        self,
        bankroll: float,
        p_win: float,
        odds: float,
        venue: str,
        clv_execution_odds: float,
        clv_closing_odds: float,
    ) -> PortfolioSummary: ...


def _build_reference_returns(p_win: float, odds: float) -> List[float]:
    """Deterministic reference return series derived from p_win and odds.
    Produces exactly 10 values for risk metric input (≥5 required by RiskAnalyticsService).
    Losses are scaled slightly across the series so downside std is never zero.
    """
    b = odds - 1.0
    win_return = b * p_win
    loss_return = -(1.0 - p_win)
    n_wins = round(p_win * 10)
    n_losses = 10 - n_wins
    wins = [win_return] * n_wins
    losses = [loss_return * (1.0 - 0.01 * i) for i in range(n_losses)]
    return wins + losses


class PortfolioService:
    """Aggregates all analytics sub-services into one portfolio snapshot."""

    def __init__(self) -> None:
        self._bankroll = BankrollService()
        self._clv = CLVService()
        self._risk = RiskAnalyticsService()
        self._alpha = AlphaService()

    def get_portfolio(
        self,
        bankroll: float,
        p_win: float,
        odds: float,
        venue: str = "SportyBet",
        clv_execution_odds: float = 2.10,
        clv_closing_odds: float = 2.00,
    ) -> PortfolioSummary:
        kelly = self._bankroll.get_recommendation(bankroll, p_win, odds, venue)
        clv = self._clv.compute_clv(clv_execution_odds, clv_closing_odds)
        ref_returns = _build_reference_returns(p_win, odds)
        risk = self._risk.calculate_metrics(ref_returns)
        frames = self._alpha.get_frames(limit=5)
        avg_edge = (
            round(sum(f.spread_pct for f in frames) / len(frames), 4) if frames else 0.0
        )
        alpha = AlphaSummary(
            top_signals=frames,
            signal_count=len(frames),
            avg_edge=avg_edge,
        )
        return PortfolioSummary(
            bankroll=kelly,
            clv=clv,
            risk=risk,
            alpha=alpha,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )


class MockPortfolioService:
    """Deterministic test double — no sub-service calls."""

    def get_portfolio(
        self,
        bankroll: float,
        p_win: float = 0.5,
        odds: float = 2.0,
        venue: str = "SportyBet",
        clv_execution_odds: float = 2.10,
        clv_closing_odds: float = 2.00,
    ) -> PortfolioSummary:
        return PortfolioSummary(
            bankroll=KellyRecommendation(
                optimal_fraction=0.05,
                suggested_stake=round(bankroll * 0.05, 2),
                confidence_level="AGGRESSIVE",
                risk_of_ruin=0.3,
                bankroll_snapshot=bankroll,
                market_impact=0.001,
            ),
            clv=CLVReport(
                match_id="MOCK",
                execution_odds=clv_execution_odds,
                closing_odds=clv_closing_odds,
                alpha_beat=round((clv_execution_odds / clv_closing_odds) - 1.0, 4),
            ),
            risk=PortfolioRiskMetrics(
                sharpe_ratio=1.5,
                sortino_ratio=2.0,
                max_drawdown=0.05,
                volatility=0.1,
                alpha=0.02,
            ),
            alpha=AlphaSummary(
                top_signals=[],
                signal_count=0,
                avg_edge=0.0,
            ),
            generated_at="2026-01-01T00:00:00+00:00",
        )
