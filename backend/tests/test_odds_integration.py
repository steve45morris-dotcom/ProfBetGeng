"""
Phase A — Odds Integration tests (TDD RED → GREEN).

Tests for:
  1. OddsLookupService.enrich_market_signals() — new method
  2. MockOddsLookupService.enrich_market_signals() — mock double
  3. GET /api/v1/signals — val_gap_score field presence + enrichment
"""
import pytest
from fastapi.testclient import TestClient

from ..main import create_app
from ..services.auth import require_api_key
from ..services.odds_lookup import OddsLookupService, MockOddsLookupService, get_odds_lookup_service
from ..services.value_discovery_service import MarketSignal

TEST_KEY = "pbg_odds_test_key"


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_signal(teams: str, market: str, local_odds: float) -> MarketSignal:
    return MarketSignal(
        match_id="TEST-001",
        teams=teams,
        market=market,
        local_odds=local_odds,
        global_odds=2.00,
        value_score=0.05,
        signal_type="VALUE",
    )


@pytest.fixture
def client():
    """Standard client — odds_service returns None (no API key in test env)."""
    app = create_app()
    app.dependency_overrides[require_api_key] = lambda: TEST_KEY
    app.dependency_overrides[get_odds_lookup_service] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_with_mock_odds():
    """Client with MockOddsLookupService injected."""
    app = create_app()
    mock = MockOddsLookupService()
    app.dependency_overrides[require_api_key] = lambda: TEST_KEY
    app.dependency_overrides[get_odds_lookup_service] = lambda: mock
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── Unit — OddsLookupService.enrich_market_signals() ─────────────────────────

class TestEnrichMarketSignals:

    @pytest.mark.asyncio
    async def test_enriches_home_market(self):
        """val_gap_score = (local / fair) - 1 for 1X2_HOME markets."""
        svc = OddsLookupService(api_key="test")
        svc._cache = {"Liverpool vs Arsenal": {"home": 2.00}}
        svc._cache_at = float("inf")

        sig = _make_signal("Liverpool vs Arsenal", "1X2_HOME", 2.20)
        await svc.enrich_market_signals([sig])

        assert sig.val_gap_score == pytest.approx(0.10, abs=0.001)

    @pytest.mark.asyncio
    async def test_enriches_draw_market(self):
        svc = OddsLookupService(api_key="test")
        svc._cache = {"PSG vs Dortmund": {"draw": 3.40}}
        svc._cache_at = float("inf")

        sig = _make_signal("PSG vs Dortmund", "1X2_DRAW", 3.70)
        await svc.enrich_market_signals([sig])

        assert sig.val_gap_score == pytest.approx(0.0882, abs=0.001)

    @pytest.mark.asyncio
    async def test_enriches_away_market(self):
        svc = OddsLookupService(api_key="test")
        svc._cache = {"Juventus vs Inter Milan": {"away": 2.60}}
        svc._cache_at = float("inf")

        sig = _make_signal("Juventus vs Inter Milan", "1X2_AWAY", 2.65)
        await svc.enrich_market_signals([sig])

        assert sig.val_gap_score == pytest.approx(0.0192, abs=0.001)

    @pytest.mark.asyncio
    async def test_skips_non_1x2_market(self):
        """BTTS, O2.5 markets should leave val_gap_score untouched."""
        svc = OddsLookupService(api_key="test")
        svc._cache = {"Real Madrid vs Barcelona": {"home": 1.85}}
        svc._cache_at = float("inf")

        sig = _make_signal("Real Madrid vs Barcelona", "O2.5_GOALS", 1.95)
        await svc.enrich_market_signals([sig])

        assert sig.val_gap_score == 0.0

    @pytest.mark.asyncio
    async def test_skips_btts_market(self):
        svc = OddsLookupService(api_key="test")
        svc._cache = {"Man City vs Man Utd": {"home": 1.72}}
        svc._cache_at = float("inf")

        sig = _make_signal("Man City vs Man Utd", "BTTS_YES", 1.72)
        await svc.enrich_market_signals([sig])

        assert sig.val_gap_score == 0.0

    @pytest.mark.asyncio
    async def test_fallback_when_no_match_in_cache(self):
        """Unknown teams → val_gap_score stays 0.0, no exception raised."""
        svc = OddsLookupService(api_key="test")
        svc._cache = {}
        svc._cache_at = float("inf")

        sig = _make_signal("Unknown FC vs Mystery United", "1X2_HOME", 2.0)
        await svc.enrich_market_signals([sig])

        assert sig.val_gap_score == 0.0

    @pytest.mark.asyncio
    async def test_no_op_when_api_key_empty(self):
        """No API key → skip enrichment entirely, no network call attempted."""
        svc = OddsLookupService(api_key="")

        sig = _make_signal("Liverpool vs Arsenal", "1X2_HOME", 2.20)
        await svc.enrich_market_signals([sig])

        assert sig.val_gap_score == 0.0

    @pytest.mark.asyncio
    async def test_enriches_multiple_signals(self):
        """All enrichable signals in a batch are processed."""
        svc = OddsLookupService(api_key="test")
        svc._cache = {
            "Liverpool vs Arsenal": {"home": 2.00},
            "PSG vs Dortmund": {"draw": 3.40},
        }
        svc._cache_at = float("inf")

        signals = [
            _make_signal("Liverpool vs Arsenal", "1X2_HOME", 2.20),
            _make_signal("PSG vs Dortmund", "1X2_DRAW", 3.70),
            _make_signal("Man City vs Man Utd", "BTTS_YES", 1.72),  # skip
        ]
        await svc.enrich_market_signals(signals)

        assert signals[0].val_gap_score == pytest.approx(0.10, abs=0.001)
        assert signals[1].val_gap_score == pytest.approx(0.0882, abs=0.001)
        assert signals[2].val_gap_score == 0.0  # BTTS skipped

    @pytest.mark.asyncio
    async def test_empty_list_is_no_op(self):
        """Empty signal list should not raise."""
        svc = OddsLookupService(api_key="test")
        svc._cache = {}
        svc._cache_at = float("inf")
        await svc.enrich_market_signals([])  # must not raise


# ── Unit — MockOddsLookupService.enrich_market_signals() ─────────────────────

class TestMockEnrichMarketSignals:

    @pytest.mark.asyncio
    async def test_mock_enriches_home(self):
        sig = _make_signal("Any Team", "1X2_HOME", 2.0)
        await MockOddsLookupService().enrich_market_signals([sig])
        assert sig.val_gap_score == 0.05

    @pytest.mark.asyncio
    async def test_mock_enriches_draw(self):
        sig = _make_signal("Any Team", "1X2_DRAW", 3.5)
        await MockOddsLookupService().enrich_market_signals([sig])
        assert sig.val_gap_score == 0.05

    @pytest.mark.asyncio
    async def test_mock_enriches_away(self):
        sig = _make_signal("Any Team", "1X2_AWAY", 2.5)
        await MockOddsLookupService().enrich_market_signals([sig])
        assert sig.val_gap_score == 0.05

    @pytest.mark.asyncio
    async def test_mock_skips_non_1x2(self):
        sig = _make_signal("Any Team", "BTTS_YES", 1.72)
        await MockOddsLookupService().enrich_market_signals([sig])
        assert sig.val_gap_score == 0.0


# ── API — GET /api/v1/signals ─────────────────────────────────────────────────

class TestSignalsEndpointOddsEnrichment:

    def test_signals_response_includes_val_gap_score_field(self, client):
        """Every signal in response must have val_gap_score field."""
        resp = client.get("/api/v1/signals?limit=3", headers={"X-API-Key": TEST_KEY})
        assert resp.status_code == 200
        for sig in resp.json()["signals"]:
            assert "val_gap_score" in sig

    def test_signals_val_gap_score_is_float(self, client):
        resp = client.get("/api/v1/signals?limit=1", headers={"X-API-Key": TEST_KEY})
        assert resp.status_code == 200
        sig = resp.json()["signals"][0]
        assert isinstance(sig["val_gap_score"], float)

    def test_signals_val_gap_defaults_to_zero_when_no_odds_service(self, client):
        """With no odds_service (None injected), val_gap_score must be 0.0."""
        resp = client.get("/api/v1/signals?limit=5", headers={"X-API-Key": TEST_KEY})
        assert resp.status_code == 200
        for sig in resp.json()["signals"]:
            assert sig["val_gap_score"] == 0.0

    def test_signals_val_gap_enriched_by_mock_service(self, client_with_mock_odds):
        """With MockOddsLookupService, 1X2 signals get val_gap_score=0.05."""
        resp = client_with_mock_odds.get(
            "/api/v1/signals?limit=10", headers={"X-API-Key": TEST_KEY}
        )
        assert resp.status_code == 200
        signals = resp.json()["signals"]
        enrichable = [
            s for s in signals
            if any(m in s["market"] for m in ("HOME", "DRAW", "AWAY"))
        ]
        assert len(enrichable) > 0, "Expected at least one 1X2 signal in simulation"
        for sig in enrichable:
            assert sig["val_gap_score"] == pytest.approx(0.05)

    def test_signals_endpoint_still_returns_200_with_odds_service(self, client_with_mock_odds):
        """Odds enrichment must not break the endpoint or change status code."""
        resp = client_with_mock_odds.get(
            "/api/v1/signals?limit=5", headers={"X-API-Key": TEST_KEY}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "signals" in data
        assert "count" in data
        assert data["count"] == len(data["signals"])
