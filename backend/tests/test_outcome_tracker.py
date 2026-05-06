"""
Tests for OutcomeService and outcome tracking endpoints (M8-c / v1.1.1).
RED phase: all tests fail until outcome_service.py and endpoints are written.
"""
import pytest
from fastapi.testclient import TestClient
from ..main import create_app
from ..services.auth import require_api_key

OWNER_KEY = "pbg_outcome_test_key"
OTHER_KEY = "pbg_outcome_other_key"


@pytest.fixture
def outcome_service():
    from ..services.outcome_service import MockOutcomeService
    return MockOutcomeService()


@pytest.fixture
def client(outcome_service):
    app = create_app()
    from ..routes import get_outcome_service
    app.dependency_overrides[require_api_key] = lambda: OWNER_KEY
    app.dependency_overrides[get_outcome_service] = lambda: outcome_service
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_auth():
    app = create_app()
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Unit — MockOutcomeService
# ---------------------------------------------------------------------------

class TestMockOutcomeServiceRecord:
    def setup_method(self):
        from ..services.outcome_service import MockOutcomeService
        self.svc = MockOutcomeService()

    def test_record_returns_ticket_outcome(self):
        from ..services.outcome_service import TicketOutcome
        result = self.svc.record_outcome(OWNER_KEY, "ABC123", "WIN", 2.5)
        assert isinstance(result, TicketOutcome)

    def test_record_sets_correct_ticket_ref(self):
        result = self.svc.record_outcome(OWNER_KEY, "XYZ789", "LOSS", None)
        assert result.ticket_ref == "XYZ789"

    def test_record_sets_correct_api_key(self):
        result = self.svc.record_outcome(OWNER_KEY, "REF001", "WIN", 1.9)
        assert result.api_key == OWNER_KEY

    def test_record_sets_correct_outcome(self):
        for outcome in ("WIN", "LOSS", "VOID", "PENDING"):
            result = self.svc.record_outcome(OWNER_KEY, "REF", outcome, None)
            assert result.outcome == outcome

    def test_record_assigns_unique_ids(self):
        r1 = self.svc.record_outcome(OWNER_KEY, "A", "WIN", 2.0)
        r2 = self.svc.record_outcome(OWNER_KEY, "B", "LOSS", None)
        assert r1.id != r2.id

    def test_record_stores_payout_odds(self):
        result = self.svc.record_outcome(OWNER_KEY, "REF", "WIN", 3.5)
        assert result.payout_odds == 3.5

    def test_record_stores_none_payout_for_loss(self):
        result = self.svc.record_outcome(OWNER_KEY, "REF", "LOSS", None)
        assert result.payout_odds is None


class TestMockOutcomeServicePerformance:
    def setup_method(self):
        from ..services.outcome_service import MockOutcomeService
        self.svc = MockOutcomeService()

    def test_empty_history_returns_safe_defaults(self):
        from ..services.outcome_service import PerformanceStats
        stats = self.svc.get_performance_stats(OWNER_KEY)
        assert isinstance(stats, PerformanceStats)
        assert stats.total_settled == 0
        assert stats.win_rate == 0.0
        assert stats.roi_estimate == 0.0

    def test_win_rate_correct_for_all_wins(self):
        self.svc.record_outcome(OWNER_KEY, "A", "WIN", 2.0)
        self.svc.record_outcome(OWNER_KEY, "B", "WIN", 1.8)
        stats = self.svc.get_performance_stats(OWNER_KEY)
        assert stats.win_rate == 1.0

    def test_win_rate_correct_for_mixed_results(self):
        self.svc.record_outcome(OWNER_KEY, "A", "WIN", 2.0)
        self.svc.record_outcome(OWNER_KEY, "B", "LOSS", None)
        self.svc.record_outcome(OWNER_KEY, "C", "LOSS", None)
        stats = self.svc.get_performance_stats(OWNER_KEY)
        assert abs(stats.win_rate - 1/3) < 0.001

    def test_void_excluded_from_win_rate_denominator(self):
        self.svc.record_outcome(OWNER_KEY, "A", "WIN", 2.0)
        self.svc.record_outcome(OWNER_KEY, "B", "VOID", None)
        # win_rate = 1 win / 1 decided = 1.0 (VOID excluded)
        stats = self.svc.get_performance_stats(OWNER_KEY)
        assert stats.win_rate == 1.0

    def test_pending_excluded_from_win_rate_denominator(self):
        self.svc.record_outcome(OWNER_KEY, "A", "WIN", 2.0)
        self.svc.record_outcome(OWNER_KEY, "B", "PENDING", None)
        stats = self.svc.get_performance_stats(OWNER_KEY)
        assert stats.win_rate == 1.0

    def test_counts_correct(self):
        self.svc.record_outcome(OWNER_KEY, "A", "WIN", 2.0)
        self.svc.record_outcome(OWNER_KEY, "B", "WIN", 1.9)
        self.svc.record_outcome(OWNER_KEY, "C", "LOSS", None)
        self.svc.record_outcome(OWNER_KEY, "D", "VOID", None)
        self.svc.record_outcome(OWNER_KEY, "E", "PENDING", None)
        stats = self.svc.get_performance_stats(OWNER_KEY)
        assert stats.wins == 2
        assert stats.losses == 1
        assert stats.voids == 1
        assert stats.pending == 1
        assert stats.total_settled == 5

    def test_stats_scoped_to_api_key(self):
        self.svc.record_outcome(OWNER_KEY, "A", "WIN", 2.0)
        self.svc.record_outcome(OTHER_KEY, "B", "WIN", 3.0)
        self.svc.record_outcome(OTHER_KEY, "C", "WIN", 2.5)
        stats = self.svc.get_performance_stats(OWNER_KEY)
        assert stats.wins == 1
        assert stats.total_settled == 1


# ---------------------------------------------------------------------------
# HTTP — POST /api/v1/tickets/{ticket_ref}/settle
# ---------------------------------------------------------------------------

class TestSettleEndpoint:
    def test_win_outcome_returns_201(self, client):
        r = client.post("/api/v1/tickets/ABC123/settle", json={"outcome": "WIN", "payout_odds": 2.5})
        assert r.status_code == 201

    def test_loss_outcome_returns_201(self, client):
        r = client.post("/api/v1/tickets/ABC123/settle", json={"outcome": "LOSS"})
        assert r.status_code == 201

    def test_void_outcome_returns_201(self, client):
        r = client.post("/api/v1/tickets/ABC123/settle", json={"outcome": "VOID"})
        assert r.status_code == 201

    def test_pending_outcome_returns_201(self, client):
        r = client.post("/api/v1/tickets/ABC123/settle", json={"outcome": "PENDING"})
        assert r.status_code == 201

    def test_invalid_outcome_returns_422(self, client):
        r = client.post("/api/v1/tickets/ABC123/settle", json={"outcome": "MAYBE"})
        assert r.status_code == 422

    def test_missing_outcome_returns_422(self, client):
        r = client.post("/api/v1/tickets/ABC123/settle", json={})
        assert r.status_code == 422

    def test_response_has_id_ticket_ref_outcome(self, client):
        data = client.post(
            "/api/v1/tickets/REF999/settle",
            json={"outcome": "WIN", "payout_odds": 1.9},
        ).json()
        assert "id" in data
        assert data["ticket_ref"] == "REF999"
        assert data["outcome"] == "WIN"

    def test_payout_odds_stored_in_response(self, client):
        data = client.post(
            "/api/v1/tickets/X1/settle",
            json={"outcome": "WIN", "payout_odds": 3.1},
        ).json()
        assert data["payout_odds"] == 3.1

    def test_401_without_api_key(self, client_no_auth):
        r = client_no_auth.post("/api/v1/tickets/X/settle", json={"outcome": "WIN"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# HTTP — GET /api/v1/analytics/performance
# ---------------------------------------------------------------------------

class TestPerformanceEndpoint:
    def test_returns_200(self, client):
        r = client.get("/api/v1/analytics/performance")
        assert r.status_code == 200

    def test_response_has_all_required_fields(self, client):
        data = client.get("/api/v1/analytics/performance").json()
        for field in ("total_settled", "wins", "losses", "voids", "pending",
                      "win_rate", "avg_payout_odds", "roi_estimate"):
            assert field in data, f"missing field: {field}"

    def test_zero_win_rate_when_no_history(self, client):
        data = client.get("/api/v1/analytics/performance").json()
        assert data["win_rate"] == 0.0
        assert data["total_settled"] == 0

    def test_win_rate_reflects_settled_outcomes(self, client, outcome_service):
        outcome_service.record_outcome(OWNER_KEY, "A", "WIN", 2.0)
        outcome_service.record_outcome(OWNER_KEY, "B", "LOSS", None)
        data = client.get("/api/v1/analytics/performance").json()
        assert abs(data["win_rate"] - 0.5) < 0.001

    def test_401_without_api_key(self, client_no_auth):
        r = client_no_auth.get("/api/v1/analytics/performance")
        assert r.status_code == 401
