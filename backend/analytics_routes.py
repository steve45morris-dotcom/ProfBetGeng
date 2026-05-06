"""
Analytics routes — M8-d split (v1.1.2).
All analytics, signals, portfolio, and outcome-performance endpoints.
"""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security
from fastapi.responses import Response

from .services.alpha_service import AlphaService
from .services.auth import require_api_key
from .services.bankroll_service import BankrollService, KellyRecommendation
from .services.clv_service import CLVService, CLVReport as CLVReportModel
from .services.limiter_config import limiter
from .services.odds_lookup import get_odds_lookup_service
from .services.outcome_service import MockOutcomeService, OutcomeService, PerformanceStats, TicketOutcome
from pydantic import BaseModel
from .services.portfolio_service import PortfolioService, PortfolioSummary
from .services.risk_analytics_service import RiskAnalyticsService, PortfolioRiskMetrics
from .services.strategy_service import StrategyService
from .services.supabase_client import get_supabase_client
from .services.value_discovery_service import ValueDiscoveryService
from .services.whale_tracker_service import WhaleTrackerService

analytics_router = APIRouter()

_risk_service = RiskAnalyticsService()
_clv_service = CLVService()
_discovery_service = ValueDiscoveryService()
_whale_service = WhaleTrackerService()
_bankroll_service = BankrollService()
_alpha_service = AlphaService()
_strategy_service = StrategyService()
_portfolio_service = PortfolioService()


def get_outcome_service():
    client = get_supabase_client()
    return OutcomeService(client) if client else MockOutcomeService()


class SettleRequest(BaseModel):
    outcome: Literal["WIN", "LOSS", "VOID", "PENDING"]
    payout_odds: Optional[float] = None


@analytics_router.post("/api/v1/tickets/{ticket_ref}/settle", response_model=TicketOutcome, status_code=201)
async def settle_ticket(
    ticket_ref: str,
    body: SettleRequest,
    api_key: str = Depends(require_api_key),
    outcome_service=Depends(get_outcome_service),
):
    return outcome_service.record_outcome(api_key, ticket_ref, body.outcome, body.payout_odds)


@analytics_router.get("/api/v1/mesh/nodes")
async def get_mesh_nodes(_: str = Depends(require_api_key)):
    return {"nodes": [
        {"id": "NODE_US_EAST", "region": "US-EAST",  "endpoint": "sgn-us-east.pbg.internal",  "latency_ms": 12, "status": "ONLINE"},
        {"id": "NODE_EU_WEST", "region": "EU-WEST",  "endpoint": "sgn-eu-west.pbg.internal",  "latency_ms": 28, "status": "ONLINE"},
        {"id": "NODE_AF_LAGO", "region": "AF-LAGOS", "endpoint": "sgn-af-lagos.pbg.internal", "latency_ms": 45, "status": "ONLINE"},
    ]}


@analytics_router.get("/api/v1/quant/arbs")
async def get_arb_windows(
    limit: int = Query(5, ge=1, le=20),
    _: str = Security(require_api_key),
):
    windows = _strategy_service.get_arb_windows(limit=limit)
    return {"windows": [w.model_dump() for w in windows]}


@analytics_router.get("/api/v1/analytics/risk", response_model=PortfolioRiskMetrics)
async def get_risk_metrics(
    returns: str = Query(..., description="Comma-separated list of percentage returns"),
    _: str = Depends(require_api_key),
):
    try:
        parsed: List[float] = [float(v.strip()) for v in returns.split(",") if v.strip()]
    except ValueError:
        raise HTTPException(status_code=422, detail="returns must be comma-separated floats")
    return _risk_service.calculate_metrics(parsed)


@analytics_router.get("/api/v1/analytics/clv", response_model=CLVReportModel)
async def get_clv(
    execution_odds: float = Query(..., description="Odds at time of bet placement"),
    closing_odds: float = Query(..., description="Final market odds before event start"),
    match_id: str = Query("unknown", description="Optional match identifier"),
    _: str = Depends(require_api_key),
):
    return _clv_service.compute_clv(
        execution_odds=execution_odds,
        closing_odds=closing_odds,
        match_id=match_id,
    )


@analytics_router.get("/api/v1/analytics/whales")
async def get_whale_pulses(
    limit: int = Query(10, ge=1, le=20),
    _: str = Depends(require_api_key),
):
    pulses = _whale_service.get_pulses(limit=limit)
    return {"pulses": [p.model_dump() for p in pulses], "count": len(pulses)}


@analytics_router.get("/api/v1/analytics/performance", response_model=PerformanceStats)
async def get_performance(
    api_key: str = Depends(require_api_key),
    outcome_service=Depends(get_outcome_service),
):
    return outcome_service.get_performance_stats(api_key)


@analytics_router.get("/api/v1/alpha/signals")
@limiter.limit("60/minute")
async def get_alpha_signals(
    request: Request,
    response: Response,
    limit: int = Query(10, ge=1, le=20),
    _: str = Depends(require_api_key),
):
    frames = _alpha_service.get_frames(limit=limit)
    return {"frames": [f.model_dump() for f in frames], "count": len(frames)}


@analytics_router.get("/api/v1/bankroll/size", response_model=KellyRecommendation)
@limiter.limit("30/minute")
async def get_bankroll_size(
    request: Request,
    response: Response,
    bankroll: float = Query(..., gt=0, description="Current bankroll amount"),
    p_win: float = Query(..., gt=0, le=1, description="Estimated win probability"),
    odds: float = Query(..., gt=1, description="Decimal odds"),
    venue: str = Query("SportyBet", description="Bookmaker venue"),
    _: str = Depends(require_api_key),
):
    return _bankroll_service.get_recommendation(bankroll, p_win, odds, venue)


@analytics_router.get("/api/v1/portfolio", response_model=PortfolioSummary)
async def get_portfolio(
    bankroll: float = Query(..., gt=0, description="Current bankroll amount"),
    p_win: float = Query(..., gt=0, le=1, description="Estimated win probability"),
    odds: float = Query(..., gt=1, description="Decimal odds"),
    venue: str = Query("SportyBet", description="Bookmaker venue"),
    clv_execution_odds: float = Query(2.10, gt=1, description="Execution odds for CLV comparison"),
    clv_closing_odds: float = Query(2.00, gt=1, description="Closing odds for CLV comparison"),
    _: str = Depends(require_api_key),
):
    return _portfolio_service.get_portfolio(
        bankroll, p_win, odds, venue, clv_execution_odds, clv_closing_odds
    )


@analytics_router.get("/api/v1/signals")
@limiter.limit("60/minute")
async def get_signals(
    request: Request,
    response: Response,
    limit: int = Query(20, ge=1, le=50),
    _: str = Depends(require_api_key),
    odds_service=Depends(get_odds_lookup_service),
):
    signals = _discovery_service.get_signals(limit=limit)
    if odds_service is not None:
        await odds_service.enrich_market_signals(signals)
    return {"signals": [s.model_dump() for s in signals], "count": len(signals)}
