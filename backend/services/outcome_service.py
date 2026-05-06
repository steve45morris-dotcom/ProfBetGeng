"""
Outcome Tracker Service — M8-c (v1.1.1).
Records ticket settlement outcomes and computes performance statistics.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional, Protocol

from pydantic import BaseModel

logger = logging.getLogger("pbg.outcome_service")

OutcomeType = Literal["WIN", "LOSS", "VOID", "PENDING"]


class TicketOutcome(BaseModel):
    id: str
    api_key: str
    ticket_ref: str
    outcome: str
    payout_odds: Optional[float]
    settled_at: str


class PerformanceStats(BaseModel):
    total_settled: int
    wins: int
    losses: int
    voids: int
    pending: int
    win_rate: float
    avg_payout_odds: float
    roi_estimate: float


class OutcomeServiceProtocol(Protocol):
    def record_outcome(
        self,
        api_key: str,
        ticket_ref: str,
        outcome: str,
        payout_odds: Optional[float],
    ) -> TicketOutcome: ...

    def get_performance_stats(self, api_key: str) -> PerformanceStats: ...


def _compute_stats(records: List[TicketOutcome]) -> PerformanceStats:
    wins = sum(1 for r in records if r.outcome == "WIN")
    losses = sum(1 for r in records if r.outcome == "LOSS")
    voids = sum(1 for r in records if r.outcome == "VOID")
    pending = sum(1 for r in records if r.outcome == "PENDING")
    decided = wins + losses
    win_rate = wins / decided if decided > 0 else 0.0
    win_odds = [r.payout_odds for r in records if r.outcome == "WIN" and r.payout_odds is not None]
    avg_payout_odds = round(sum(win_odds) / len(win_odds), 4) if win_odds else 0.0
    roi_estimate = round((avg_payout_odds - 1.0) * win_rate - (1.0 - win_rate), 4) if decided > 0 else 0.0
    return PerformanceStats(
        total_settled=len(records),
        wins=wins,
        losses=losses,
        voids=voids,
        pending=pending,
        win_rate=round(win_rate, 6),
        avg_payout_odds=avg_payout_odds,
        roi_estimate=roi_estimate,
    )


class MockOutcomeService:
    """In-memory test double — no Supabase dependency."""

    def __init__(self) -> None:
        self._store: List[TicketOutcome] = []

    def record_outcome(
        self,
        api_key: str,
        ticket_ref: str,
        outcome: str,
        payout_odds: Optional[float],
    ) -> TicketOutcome:
        record = TicketOutcome(
            id=str(uuid.uuid4()),
            api_key=api_key,
            ticket_ref=ticket_ref,
            outcome=outcome,
            payout_odds=payout_odds,
            settled_at=datetime.now(timezone.utc).isoformat(),
        )
        self._store.append(record)
        return record

    def get_performance_stats(self, api_key: str) -> PerformanceStats:
        scoped = [r for r in self._store if r.api_key == api_key]
        return _compute_stats(scoped)


class OutcomeService:
    """Supabase-backed production implementation."""

    def __init__(self, client) -> None:
        self._client = client

    def record_outcome(
        self,
        api_key: str,
        ticket_ref: str,
        outcome: str,
        payout_odds: Optional[float],
    ) -> TicketOutcome:
        row = {
            "api_key": api_key,
            "ticket_ref": ticket_ref,
            "outcome": outcome,
            "payout_odds": payout_odds,
        }
        result = self._client.table("ticket_outcomes").insert(row).execute()
        data = result.data[0]
        return TicketOutcome(
            id=data["id"],
            api_key=data["api_key"],
            ticket_ref=data["ticket_ref"],
            outcome=data["outcome"],
            payout_odds=data.get("payout_odds"),
            settled_at=data["settled_at"],
        )

    def get_performance_stats(self, api_key: str) -> PerformanceStats:
        result = (
            self._client.table("ticket_outcomes")
            .select("*")
            .eq("api_key", api_key)
            .execute()
        )
        records = [
            TicketOutcome(
                id=row["id"],
                api_key=row["api_key"],
                ticket_ref=row["ticket_ref"],
                outcome=row["outcome"],
                payout_odds=row.get("payout_odds"),
                settled_at=row["settled_at"],
            )
            for row in result.data
        ]
        return _compute_stats(records)
