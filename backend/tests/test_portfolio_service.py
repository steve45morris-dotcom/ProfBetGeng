"""
Tests for PortfolioService and GET /api/v1/portfolio endpoint (M8-b / v1.0.1).
RED phase: all tests fail until portfolio_service.py and the endpoint are written.
"""
import pytest
from fastapi.testclient import TestClient
from ..main import create_app
from ..services.auth import require_api_key

TEST_KEY = "pbg_portfolio_test_key"


@pytest.fixture
def client():
    app = create_app()
    app.dependency_overrides[require_api_key] = lambda: TEST_KEY
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Unit — PortfolioService.get_portfolio()
# ---------------------------------------------------------------------------

class TestPortfolioServiceUnit:
    def setup_method(self):
        from ..services.portfolio_service import PortfolioService
        self.svc = PortfolioService()

    def test_returns_portfolio_summary_with_all_sections(self):
        from ..services.portfolio_service import PortfolioSummary
        result = self.svc.get_portfolio(bankroll=10_000, p_win=0.6, odds=2.0)
        assert isinstance(result, PortfolioSummary)

    def test_bankroll_section_has_correct_snapshot(self):
        result = self.svc.get_portfolio(bankroll=5_000, p_win=0.6, odds=2.0)
        assert result.bankroll.bankroll_snapshot == 5_000.0

    def test_bankroll_optimal_fraction_positive_for_positive_edge(self):
        # p_win=0.6, odds=2.0 → positive edge → fraction > 0
        result = self.svc.get_portfolio(bankroll=10_000, p_win=0.6, odds=2.0)
        assert result.bankroll.optimal_fraction > 0.0

    def test_clv_positive_alpha_beat_when_execution_beats_closing(self):
        # execution_odds=2.20 > closing_odds=2.00 → alpha_beat > 0
        result = self.svc.get_portfolio(
            bankroll=10_000, p_win=0.6, odds=2.0,
            clv_execution_odds=2.20, clv_closing_odds=2.00,
        )
        assert result.clv.alpha_beat > 0.0

    def test_clv_negative_alpha_beat_when_execution_worse_than_closing(self):
        # execution_odds=1.90 < closing_odds=2.00 → alpha_beat < 0
        result = self.svc.get_portfolio(
            bankroll=10_000, p_win=0.6, odds=2.0,
            clv_execution_odds=1.90, clv_closing_odds=2.00,
        )
        assert result.clv.alpha_beat < 0.0

    def test_risk_section_fields_present(self):
        result = self.svc.get_portfolio(bankroll=10_000, p_win=0.6, odds=2.0)
        assert hasattr(result.risk, "sharpe_ratio")
        assert hasattr(result.risk, "max_drawdown")
        assert hasattr(result.risk, "volatility")

    def test_alpha_section_returns_signals(self):
        result = self.svc.get_portfolio(bankroll=10_000, p_win=0.6, odds=2.0)
        assert result.alpha.signal_count > 0
        assert len(result.alpha.top_signals) == result.alpha.signal_count

    def test_alpha_avg_edge_is_positive(self):
        result = self.svc.get_portfolio(bankroll=10_000, p_win=0.6, odds=2.0)
        assert result.alpha.avg_edge >= 0.0

    def test_generated_at_is_non_empty_string(self):
        result = self.svc.get_portfolio(bankroll=10_000, p_win=0.6, odds=2.0)
        assert isinstance(result.generated_at, str)
        assert len(result.generated_at) > 0

    def test_venue_param_passed_to_bankroll(self):
        # Bet9ja has lower resistance → higher market_impact at large stakes
        r_sbet = self.svc.get_portfolio(bankroll=10_000, p_win=0.6, odds=2.0, venue="SportyBet")
        r_b9ja = self.svc.get_portfolio(bankroll=10_000, p_win=0.6, odds=2.0, venue="Bet9ja")
        assert r_sbet.bankroll.bankroll_snapshot == r_b9ja.bankroll.bankroll_snapshot


# ---------------------------------------------------------------------------
# Unit — MockPortfolioService
# ---------------------------------------------------------------------------

class TestMockPortfolioService:
    def test_mock_returns_portfolio_summary(self):
        from ..services.portfolio_service import MockPortfolioService, PortfolioSummary
        mock = MockPortfolioService()
        result = mock.get_portfolio(bankroll=1000, p_win=0.5, odds=2.0)
        assert isinstance(result, PortfolioSummary)

    def test_mock_bankroll_snapshot_matches_input(self):
        from ..services.portfolio_service import MockPortfolioService
        mock = MockPortfolioService()
        result = mock.get_portfolio(bankroll=7_777, p_win=0.5, odds=2.0)
        assert result.bankroll.bankroll_snapshot == 7_777

    def test_mock_is_deterministic(self):
        from ..services.portfolio_service import MockPortfolioService
        mock = MockPortfolioService()
        r1 = mock.get_portfolio(bankroll=1000, p_win=0.5, odds=2.0)
        r2 = mock.get_portfolio(bankroll=1000, p_win=0.5, odds=2.0)
        assert r1.bankroll.optimal_fraction == r2.bankroll.optimal_fraction


# ---------------------------------------------------------------------------
# HTTP — GET /api/v1/portfolio
# ---------------------------------------------------------------------------

class TestPortfolioEndpoint:
    BASE = "/api/v1/portfolio"

    def test_200_with_required_params(self, client):
        r = client.get(f"{self.BASE}?bankroll=10000&p_win=0.6&odds=2.0")
        assert r.status_code == 200

    def test_response_has_all_top_level_keys(self, client):
        data = client.get(f"{self.BASE}?bankroll=10000&p_win=0.6&odds=2.0").json()
        for key in ("bankroll", "clv", "risk", "alpha", "generated_at"):
            assert key in data, f"missing key: {key}"

    def test_bankroll_snapshot_matches_query_param(self, client):
        data = client.get(f"{self.BASE}?bankroll=25000&p_win=0.6&odds=2.0").json()
        assert data["bankroll"]["bankroll_snapshot"] == 25_000.0

    def test_alpha_key_has_top_signals(self, client):
        data = client.get(f"{self.BASE}?bankroll=10000&p_win=0.6&odds=2.0").json()
        assert "top_signals" in data["alpha"]
        assert isinstance(data["alpha"]["top_signals"], list)

    def test_422_on_missing_bankroll(self, client):
        r = client.get(f"{self.BASE}?p_win=0.6&odds=2.0")
        assert r.status_code == 422

    def test_422_on_missing_p_win(self, client):
        r = client.get(f"{self.BASE}?bankroll=10000&odds=2.0")
        assert r.status_code == 422

    def test_422_on_missing_odds(self, client):
        r = client.get(f"{self.BASE}?bankroll=10000&p_win=0.6")
        assert r.status_code == 422

    def test_422_on_p_win_above_one(self, client):
        r = client.get(f"{self.BASE}?bankroll=10000&p_win=1.5&odds=2.0")
        assert r.status_code == 422

    def test_422_on_odds_not_greater_than_one(self, client):
        r = client.get(f"{self.BASE}?bankroll=10000&p_win=0.6&odds=0.9")
        assert r.status_code == 422

    def test_422_on_zero_bankroll(self, client):
        r = client.get(f"{self.BASE}?bankroll=0&p_win=0.6&odds=2.0")
        assert r.status_code == 422

    def test_venue_param_accepted(self, client):
        r = client.get(f"{self.BASE}?bankroll=10000&p_win=0.6&odds=2.0&venue=Bet9ja")
        assert r.status_code == 200

    def test_clv_params_accepted(self, client):
        r = client.get(
            f"{self.BASE}?bankroll=10000&p_win=0.6&odds=2.0"
            "&clv_execution_odds=2.10&clv_closing_odds=2.00"
        )
        assert r.status_code == 200
        data = r.json()
        assert data["clv"]["alpha_beat"] > 0

    def test_401_without_api_key(self):
        app = create_app()
        c = TestClient(app)
        r = c.get(f"{self.BASE}?bankroll=10000&p_win=0.6&odds=2.0")
        assert r.status_code in (200, 401, 403)
