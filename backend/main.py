"""
ProfBetGeng — FastAPI Entry Point
"""
import asyncio
import logging
from contextlib import asynccontextmanager

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from .services.limiter_config import limiter

from .routes import router
from .syndicate_routes import syndicate_router
from .analytics_routes import analytics_router
from .admin_routes import admin_router
from .config import get_settings
from .services.pbg_streaming_protocol import LiveOddsEngine, live_odds_manager

logger = logging.getLogger(__name__)


def _init_sentry(settings) -> None:
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=settings.sentry_traces_sample_rate,
        environment=settings.environment,
        release=f"profbetgeng@{settings.app_version}",
    )
    logger.info("Sentry error monitoring enabled.")

pulse_odds_engine = LiveOddsEngine(live_odds_manager)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if settings.environment == "production" and settings.admin_token in ("", "pbg_admin_secret"):
        raise RuntimeError(
            "ADMIN_TOKEN env var must be set to a strong secret in production"
        )
    logger.info(f"PBG {settings.app_version} starting — env: {settings.environment}")
    print(f"PBG {settings.app_version} starting — env: {settings.environment}")
    odds_task = asyncio.create_task(pulse_odds_engine.start_stream())
    yield
    pulse_odds_engine.stop_stream()
    await asyncio.gather(odds_task, return_exceptions=True)
    logger.info("PBG shutdown complete.")
    print("PBG shutdown complete.")


def create_app() -> FastAPI:
    settings = get_settings()
    _init_sentry(settings)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    app.include_router(syndicate_router)
    app.include_router(analytics_router)
    app.include_router(admin_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
