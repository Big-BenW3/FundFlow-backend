"""Rate limiting for sensitive endpoints (plan.md §4).

Sliding-window counters per client IP, per endpoint bucket:

    auth       5 req / 60s  — login / register brute-force protection
    contribute 3 req / 60s  — contribution spam / double-submit protection
    default   60 req / 60s  — everything else

Usage (FastAPI dependency — preserves route signatures)::

    from app.core.rate_limiter import limit

    @router.post("/login", dependencies=[Depends(limit("auth"))])
    def login(...): ...

Why a dependency and not a decorator: wrapping a route function changes
its signature, which breaks FastAPI's dependency injection (``body``,
``db``, ``user`` would stop resolving). A ``Depends`` callable runs
*before* the handler with the real ``Request`` injected by FastAPI.

Test bypass: pytest's ``TestClient`` shares one IP (``testclient``), so
the suite would trip the limiter immediately. Rate limiting is skipped
when ``PYTEST_CURRENT_TEST`` is set or ``RATE_LIMIT_ENABLED=false``.
Call :func:`reset_rate_limits` in tests that assert 429 behaviour.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from functools import wraps
from threading import Lock
from typing import Callable, TypeVar

from fastapi import HTTPException, Request, status

T = TypeVar("T")

# Rate limit configuration
RATE_LIMITS = {
    "auth": {"requests": 5, "period": 60},     # 5 requests per minute for auth endpoints
    "contribute": {"requests": 3, "period": 60},  # 3 requests per minute for contribute
    "default": {"requests": 60, "period": 60},    # 60 requests per minute default
}

# Store request counts: {endpoint_type: {ip_address: [timestamp1, timestamp2, ...]}}
_request_store: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
_request_locks: dict[str, Lock] = defaultdict(Lock)


def is_enabled() -> bool:
    """Skip limiting inside pytest or when RATE_LIMIT_ENABLED=false."""
    if os.environ.get("RATE_LIMIT_ENABLED", "").lower() == "false":
        return False
    return "PYTEST_CURRENT_TEST" not in os.environ


def reset_rate_limits() -> None:
    """Clear all counters — test hook for 429-behaviour tests."""
    for bucket in list(_request_store.keys()):
        with _request_locks[bucket]:
            _request_store[bucket].clear()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _cleanup_old_requests(timestamps: list[float], period: int) -> list[float]:
    """Remove timestamps older than the rate limit period."""
    cutoff = time.time() - period
    return [ts for ts in timestamps if ts > cutoff]


def _check_rate_limit(ip: str, endpoint_type: str) -> bool:
    """Check if the request should be allowed based on rate limits."""
    config = RATE_LIMITS.get(endpoint_type, RATE_LIMITS["default"])
    limit = config["requests"]
    period = config["period"]

    with _request_locks[endpoint_type]:
        timestamps = _request_store[endpoint_type].get(ip, [])
        current_time = time.time()

        # Clean up old requests
        timestamps = _cleanup_old_requests(timestamps, period)

        # Check if limit exceeded
        if len(timestamps) >= limit:
            return False

        # Add current request
        timestamps.append(current_time)
        _request_store[endpoint_type][ip] = timestamps
        return True


def _enforce(ip: str, endpoint_type: str) -> None:
    if not _check_rate_limit(ip, endpoint_type):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later.",
            headers={"Retry-After": "60"},
        )


def limit(endpoint_type: str = "default") -> Callable:
    """Return a FastAPI dependency enforcing ``endpoint_type`` limits."""
    async def _dependency(request: Request) -> None:
        if not is_enabled():
            return
        _enforce(_client_ip(request), endpoint_type)

    _dependency.__name__ = f"rate_limit_{endpoint_type}"
    return _dependency


auth_limit = limit("auth")
contribute_limit = limit("contribute")


def rate_limit(endpoint_type: str = "default") -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Legacy decorator (kept for backwards-compat). Prefer :func:`limit`."""
    import inspect

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        sig = inspect.signature(func)
        params = list(sig.parameters.values())
        request_arg = next(
            (i for i, p in enumerate(params) if p.annotation is Request or p.name == "request"),
            None,
        )
        is_coro = inspect.iscoroutinefunction(func)

        @wraps(func)
        async def wrapper(*args, **kwargs):
            request = kwargs.get("request")
            if request is None and request_arg is not None and request_arg < len(args):
                request = args[request_arg]
            if isinstance(request, Request) and is_enabled():
                _enforce(_client_ip(request), endpoint_type)
            if is_coro:
                return await func(*args, **kwargs)
            return func(*args, **kwargs)

        wrapper.__signature__ = sig  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


def rate_limit_auth(func: Callable[..., T]) -> Callable[..., T]:
    """Rate limiting specifically for authentication endpoints."""
    return rate_limit("auth")(func)


def rate_limit_contribute(func: Callable[..., T]) -> Callable[..., T]:
    """Rate limiting specifically for contribution endpoints."""
    return rate_limit("contribute")(func)
