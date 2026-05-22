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

    # Unified static frontend hosting (production container fallback)
    import os
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists() and frontend_dist.is_dir():
        # Mount /assets specifically for standard Vite assets bundles
        assets_dir = frontend_dist / "assets"
        if assets_dir.exists() and assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        # Catch-all route to serve other files in dist/ and fallback to index.html for React SPA routes
        @app.get("/{fallback_path:path}")
        async def serve_frontend(fallback_path: str):
            # Check if requesting a direct static file in dist (like favicon.svg, icons.svg)
            file_path = frontend_dist / fallback_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))
            
            # Default fallback to React SPA index.html
            index_file = frontend_dist / "index.html"
            if index_file.exists():
                return FileResponse(str(index_file))

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
