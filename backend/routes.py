import asyncio
import uuid as _uuid
import datetime as _datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, Security, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import Response, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from .models import (
    ConvertedTicket, SportybetTicket, ConversionRecord,
    APIKeyCreate, APIKeyResponse,
    ConvertRequest, ConvertResponse, CompositeAnalysis,
)
from .services.sportybet_parser import SportybetAdapter
from .services.converter import Bet9jaConverter
from .services.pbg_streaming_protocol import live_odds_manager
from .services.auth import APIKeyService, MockAPIKeyService, require_api_key
from .services.storage import SupabaseStorageService, MockStorageService
from .services.ticket_pulse import TicketPulseService, MockTicketPulseService, RiskReport
from .services.risk_engine import RiskEngine
from .services.sentiment import SentimentAnalysisService
from .services.odds_lookup import get_odds_lookup_service
from .services.supabase_client import get_supabase_client
from .services.limiter_config import limiter
from .config import get_settings
from .batch import BatchConvertRequest, BatchConvertResponse, BatchTicketResult, BatchSummary

# Re-exports so existing test imports (`from ..routes import get_*`) keep working
from .syndicate_routes import get_syndicate_service  # noqa: F401
from .analytics_routes import get_outcome_service    # noqa: F401

router = APIRouter()
parser = SportybetAdapter()
converter = Bet9jaConverter()


def get_auth_service():
    client = get_supabase_client()
    return APIKeyService(client) if client else MockAPIKeyService()


def get_storage_service():
    client = get_supabase_client()
    return SupabaseStorageService(client) if client else MockStorageService()


def get_pulse_service():
    settings = get_settings()
    return TicketPulseService() if settings.anthropic_api_key else MockTicketPulseService()


def get_sentiment_service():
    settings = get_settings()
    return SentimentAnalysisService(api_key=settings.anthropic_api_key or None)


def persist_conversion(storage_service, record: ConversionRecord):
    try:
        storage_service.save_conversion(record)
    except Exception as e:
        print(f"ERROR: Background persistence failed: {e}")


class AnalyseRequest(BaseModel):
    converted: ConvertedTicket
    language: str = "en"


class AnalyseResponse(BaseModel):
    success: bool
    analysis: Optional[RiskReport] = None
    error: Optional[str] = None


@router.get("/health")
async def health():
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "auth_enabled": settings.auth_enabled,
    }


@router.post("/api/v1/convert", response_model=ConvertResponse)
@limiter.limit("30/minute")
async def convert_ticket(
    request: Request,
    response: Response,
    body: ConvertRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(require_api_key),
    auth_service=Depends(get_auth_service),
    storage_service=Depends(get_storage_service),
    pulse_service=Depends(get_pulse_service),
    sentiment_service=Depends(get_sentiment_service),
    odds_service=Depends(get_odds_lookup_service),
):
    settings = get_settings()
    if settings.auth_enabled and api_key != "dev_bypass":
        if not auth_service.validate_key(api_key):
            raise HTTPException(status_code=403, detail="Invalid or inactive API key")

    sportybet_ticket = SportybetTicket(
        booking_code=body.booking_code,
        selections=body.selections,
        stake=body.stake,
    )
    internal_ticket, _ = parser.parse(sportybet_ticket)

    if odds_service is not None:
        await odds_service.enrich_val_gap(internal_ticket.selections)

    converted = converter.convert(internal_ticket)

    pulse_result = None
    metrics_result = None
    sentiment_result = None

    if body.include_analysis:
        metrics_result = RiskEngine.compute(converted, internal_ticket.selections)
        pulse_result, sentiment_result = await asyncio.gather(
            pulse_service.analyse(converted, language=body.language),
            sentiment_service.analyse(converted),
        )

    composite = CompositeAnalysis(
        pulse=pulse_result,
        metrics=metrics_result,
        sentiment=sentiment_result,
    )

    record = ConversionRecord(
        api_key=api_key,
        source_booking_code=body.booking_code,
        source_platform="sportybet",
        target_platform="bet9ja",
        selections_count=len(body.selections),
        converted_count=converted.converted_count,
        skipped_count=converted.skipped_count,
        stake=body.stake,
        total_odds=internal_ticket.total_odds,
        potential_returns=internal_ticket.potential_returns,
        risk_score=pulse_result.score if pulse_result else None,
        risk_level=pulse_result.level if pulse_result else None,
    )
    background_tasks.add_task(persist_conversion, storage_service, record)

    await live_odds_manager.broadcast_json({
        "type": "CONVERSION_SUCCESS",
        "source": "sportybet",
        "target": "bet9ja",
        "selections": converted.converted_count,
        "timestamp": _datetime.datetime.now().isoformat(),
    })

    return ConvertResponse(
        success=True,
        converted=converted,
        analysis=composite,
    )


@router.post("/api/v1/analyse", response_model=AnalyseResponse)
@limiter.limit("20/minute")
async def analyse_ticket(
    request: Request,
    response: Response,
    body: AnalyseRequest,
    api_key: str = Depends(require_api_key),
    auth_service=Depends(get_auth_service),
    pulse_service=Depends(get_pulse_service),
):
    settings = get_settings()
    if settings.auth_enabled and api_key != "dev_bypass":
        if not auth_service.validate_key(api_key):
            raise HTTPException(status_code=403, detail="Invalid or inactive API key")
    analysis = await pulse_service.analyse(body.converted, language=body.language)
    return AnalyseResponse(success=True, analysis=analysis)


@router.post("/api/v1/analyse/stream")
@limiter.limit("20/minute")
async def analyse_ticket_stream(
    request: Request,
    response: Response,
    body: AnalyseRequest,
    api_key: str = Depends(require_api_key),
    auth_service=Depends(get_auth_service),
    pulse_service=Depends(get_pulse_service),
):
    settings = get_settings()
    if settings.auth_enabled and api_key != "dev_bypass":
        if not auth_service.validate_key(api_key):
            raise HTTPException(status_code=403, detail="Invalid or inactive API key")

    generator = pulse_service.analyse_stream(body.converted, language=body.language)
    return StreamingResponse(generator, media_type="text/event-stream")


@router.get("/api/v1/history")
async def get_history(
    limit: int = 50,
    api_key: str = Depends(require_api_key),
    auth_service=Depends(get_auth_service),
    storage_service=Depends(get_storage_service),
):
    settings = get_settings()
    if settings.auth_enabled and api_key != "dev_bypass":
        if not auth_service.validate_key(api_key):
            raise HTTPException(status_code=403, detail="Invalid or inactive API key")
    records = storage_service.get_conversions(api_key=api_key, limit=limit)
    return {"records": [r.__dict__ for r in records], "count": len(records)}


@router.post("/api/v1/keys", response_model=APIKeyResponse)
@limiter.limit("5/minute")
async def create_api_key(
    request: Request,
    response: Response,
    payload: APIKeyCreate,
    admin_token: str = Security(APIKeyHeader(name="X-Admin-Token", auto_error=False)),
    auth_service=Depends(get_auth_service),
):
    settings = get_settings()
    if not admin_token or admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Invalid admin token")

    result = auth_service.generate_key(label=payload.label, owner=payload.owner)
    return APIKeyResponse(**result)


@router.post("/api/v1/convert-batch", response_model=BatchConvertResponse)
@limiter.limit("10/minute")
async def convert_batch(
    request: Request,
    response: Response,
    body: BatchConvertRequest,
    api_key: str = Depends(require_api_key),
    auth_service=Depends(get_auth_service),
    storage_service=Depends(get_storage_service),
    pulse_service=Depends(get_pulse_service),
):
    settings = get_settings()

    if not settings.batch_enabled:
        raise HTTPException(status_code=404, detail="Batch conversion is not enabled")

    if settings.auth_enabled and api_key != "dev_bypass":
        if not auth_service.validate_key(api_key):
            raise HTTPException(status_code=403, detail="Invalid or inactive API key")

    batch_id = str(_uuid.uuid4())

    async def process_one(index: int, ticket: ConvertRequest) -> BatchTicketResult:
        try:
            sportybet_ticket = SportybetTicket(
                booking_code=ticket.booking_code,
                selections=ticket.selections,
                stake=ticket.stake,
            )
            internal_ticket, _ = parser.parse(sportybet_ticket)
            converted = converter.convert(internal_ticket)

            analysis = None
            if ticket.include_analysis:
                analysis = await pulse_service.analyse(converted, language=ticket.language)

            storage_service.save_conversion(ConversionRecord(
                api_key=api_key,
                source_booking_code=ticket.booking_code,
                source_platform="sportybet",
                target_platform="bet9ja",
                selections_count=len(ticket.selections),
                converted_count=converted.converted_count,
                skipped_count=converted.skipped_count,
                stake=ticket.stake,
                total_odds=internal_ticket.total_odds,
                potential_returns=internal_ticket.potential_returns,
                risk_score=analysis.score if analysis else None,
                risk_level=analysis.level if analysis else None,
            ))

            result = ConvertResponse(success=True, converted=converted, analysis=analysis)
            return BatchTicketResult(index=index, status="success", result=result)

        except Exception as exc:
            return BatchTicketResult(index=index, status="error", error=str(exc))

    tasks = [process_one(i, t) for i, t in enumerate(body.tickets)]
    results: list[BatchTicketResult] = await asyncio.gather(*tasks)

    succeeded = sum(1 for r in results if r.status == "success")
    failed = len(results) - succeeded

    return BatchConvertResponse(
        batch_id=batch_id,
        summary=BatchSummary(total=len(results), succeeded=succeeded, failed=failed),
        results=results,
    )


@router.websocket("/api/v1/ws/odds")
async def websocket_odds_endpoint(websocket: WebSocket):
    await live_odds_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        live_odds_manager.disconnect(websocket)
