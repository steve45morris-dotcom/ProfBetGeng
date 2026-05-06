"""
Phase B — Rate Limiting tests (TDD GREEN).

Tests for:
  1. Infrastructure — limiter singleton wired to app state
  2. Behavior — 429 + Retry-After when limit exceeded, 200 within limit
  3. Per-route registry — protected endpoints are decorated with slowapi
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from ..main import create_app
from ..services.auth import require_api_key
from ..services.limiter_config import limiter

TEST_KEY = "pbg_rate_limit_test"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def limited_app():
    """Minimal app with a 1/minute route for behavioral testing."""
    _limiter = Limiter(key_func=get_remote_address, headers_enabled=True)
    app = FastAPI()
    app.state.limiter = _limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/ping")
    @_limiter.limit("1/minute")
    async def ping(request: Request, response: Response):
        return {"ok": True}

    return app


@pytest.fixture
def pbg_client():
    """PBG app client with auth bypassed."""
    app = create_app()
    app.dependency_overrides[require_api_key] = lambda: TEST_KEY
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── Infrastructure ────────────────────────────────────────────────────────────

class TestRateLimiterInfrastructure:

    def test_limiter_on_app_state(self, pbg_client):
        """app.state.limiter must be a Limiter instance."""
        assert isinstance(pbg_client.app.state.limiter, Limiter)

    def test_rate_limit_exception_handler_registered(self, pbg_client):
        """RateLimitExceeded must have a handler on the app."""
        assert RateLimitExceeded in pbg_client.app.exception_handlers


# ── Behavior (using minimal synthetic app) ───────────────────────────────────

class TestRateLimitBehavior:

    def test_within_limit_returns_200(self, limited_app):
        """Single request to limited endpoint returns 200."""
        client = TestClient(limited_app)
        resp = client.get("/ping")
        assert resp.status_code == 200

    def test_exceeding_limit_returns_429(self, limited_app):
        """Second request to 1/minute endpoint must return 429."""
        client = TestClient(limited_app)
        client.get("/ping")           # 1st — OK
        resp = client.get("/ping")    # 2nd — over limit
        assert resp.status_code == 429

    def test_retry_after_header_present_on_429(self, limited_app):
        """429 response must include Retry-After header."""
        client = TestClient(limited_app)
        client.get("/ping")
        resp = client.get("/ping")
        assert resp.status_code == 429
        lower_headers = {k.lower(): v for k, v in resp.headers.items()}
        assert "retry-after" in lower_headers

    def test_429_body_contains_error_detail(self, limited_app):
        """429 body must include a detail field indicating rate limit exceeded."""
        client = TestClient(limited_app)
        client.get("/ping")
        resp = client.get("/ping")
        assert resp.status_code == 429
        body = resp.json()
        assert "error" in body or "detail" in body


# ── Per-route registry ────────────────────────────────────────────────────────

class TestEndpointLimits:
    """
    Verify each protected endpoint is registered in the shared limiter.
    slowapi stores decorated function names in limiter._route_limits keyed
    by fully-qualified name (module.function_name).
    """

    def _route_names(self):
        return {k.split(".")[-1] for k in limiter._route_limits.keys()}

    def _limit_for(self, func_name: str):
        full_key = next((k for k in limiter._route_limits if k.endswith(f".{func_name}")), None)
        assert full_key is not None, f"{func_name} must be in route_limits"
        return limiter._route_limits[full_key][0]

    def test_convert_ticket_is_rate_limited(self):
        assert "convert_ticket" in self._route_names(), \
            "POST /api/v1/convert must have @limiter.limit() decorator"

    def test_convert_batch_is_rate_limited(self):
        assert "convert_batch" in self._route_names(), \
            "POST /api/v1/convert-batch must have @limiter.limit() decorator"

    def test_analyse_ticket_is_rate_limited(self):
        assert "analyse_ticket" in self._route_names(), \
            "POST /api/v1/analyse must have @limiter.limit() decorator"

    def test_analyse_stream_is_rate_limited(self):
        assert "analyse_ticket_stream" in self._route_names(), \
            "POST /api/v1/analyse/stream must have @limiter.limit() decorator"

    def test_get_signals_is_rate_limited(self):
        assert "get_signals" in self._route_names(), \
            "GET /api/v1/signals must have @limiter.limit() decorator"

    def test_get_alpha_signals_is_rate_limited(self):
        assert "get_alpha_signals" in self._route_names(), \
            "GET /api/v1/alpha/signals must have @limiter.limit() decorator"

    def test_get_bankroll_size_is_rate_limited(self):
        assert "get_bankroll_size" in self._route_names(), \
            "GET /api/v1/bankroll/size must have @limiter.limit() decorator"

    def test_create_api_key_is_rate_limited(self):
        assert "create_api_key" in self._route_names(), \
            "POST /api/v1/keys must have @limiter.limit() decorator"

    def test_keys_limit_is_tightest(self):
        """POST /api/v1/keys must have 5/minute — the tightest limit in the API."""
        limit_item = self._limit_for("create_api_key")
        assert limit_item.limit.amount == 5, \
            f"Expected 5/minute for /keys, got {limit_item.limit.amount}"
