"""
Limiter singleton — imported by main.py (wired to app) and routes.py (decorators).
Key function: X-API-Key header takes precedence over remote IP so that rate limits
are per-client rather than per-IP when an API key is present.
"""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def get_api_key_or_ip(request: Request) -> str:
    return request.headers.get("X-API-Key") or get_remote_address(request)


limiter = Limiter(key_func=get_api_key_or_ip, default_limits=["200/minute"], headers_enabled=True)
