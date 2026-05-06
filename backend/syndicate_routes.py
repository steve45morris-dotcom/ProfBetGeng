"""
Syndicate routes — M8-d split (v1.1.2).
All /api/v1/syndicates/* endpoints extracted from routes.py.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .services.auth import require_api_key
from .services.supabase_client import get_supabase_client
from .services.syndicate_service import MockSyndicateService, SyndicateService

syndicate_router = APIRouter()


def get_syndicate_service():
    client = get_supabase_client()
    return SyndicateService(client) if client else MockSyndicateService()


class SyndicateCreate(BaseModel):
    name: str


class MemberAdd(BaseModel):
    api_key: str


class TicketAdd(BaseModel):
    booking_code: str


@syndicate_router.post("/api/v1/syndicates", status_code=201)
async def create_syndicate(
    payload: SyndicateCreate,
    owner: str = Depends(require_api_key),
    syndicate_service=Depends(get_syndicate_service),
):
    return syndicate_service.create_syndicate(payload.name, owner)


@syndicate_router.get("/api/v1/syndicates")
async def list_syndicates(
    owner: str = Depends(require_api_key),
    syndicate_service=Depends(get_syndicate_service),
):
    return {"syndicates": syndicate_service.list_syndicates(owner)}


@syndicate_router.delete("/api/v1/syndicates/{syndicate_id}", status_code=204)
async def delete_syndicate(
    syndicate_id: str,
    owner: str = Depends(require_api_key),
    syndicate_service=Depends(get_syndicate_service),
):
    if not syndicate_service.delete_syndicate(syndicate_id, owner):
        raise HTTPException(status_code=404, detail="Syndicate not found or not owned by you")


@syndicate_router.post("/api/v1/syndicates/{syndicate_id}/members", status_code=201)
async def add_member(
    syndicate_id: str,
    payload: MemberAdd,
    _: str = Depends(require_api_key),
    syndicate_service=Depends(get_syndicate_service),
):
    result = syndicate_service.add_member(syndicate_id, payload.api_key)
    if result is None:
        raise HTTPException(status_code=404, detail="Syndicate not found")
    return result


@syndicate_router.delete("/api/v1/syndicates/{syndicate_id}/members/{member_key}", status_code=204)
async def remove_member(
    syndicate_id: str,
    member_key: str,
    _: str = Depends(require_api_key),
    syndicate_service=Depends(get_syndicate_service),
):
    if not syndicate_service.remove_member(syndicate_id, member_key):
        raise HTTPException(status_code=404, detail="Member not found in syndicate")


@syndicate_router.post("/api/v1/syndicates/{syndicate_id}/tickets", status_code=201)
async def add_syndicate_ticket(
    syndicate_id: str,
    payload: TicketAdd,
    added_by: str = Depends(require_api_key),
    syndicate_service=Depends(get_syndicate_service),
):
    result = syndicate_service.add_ticket(syndicate_id, payload.booking_code, added_by)
    if result is None:
        raise HTTPException(status_code=404, detail="Syndicate not found")
    return result
