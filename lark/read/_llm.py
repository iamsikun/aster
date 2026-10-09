"""Thin indirection to lark.llm.roles so reading code can be tested without real workers."""

from __future__ import annotations

from lark.context import RunContext
from lark.llm.base import LLMRequest, LLMResult


class _NeverRaised(Exception):
    pass


def no_route_error() -> type[Exception]:
    try:
        from lark.llm.roles import NoRouteAvailable
    except ImportError:
        return _NeverRaised
    return NoRouteAvailable


def call(ctx: RunContext, role: str, req: LLMRequest, route: str | None = None) -> LLMResult:
    """Route by role; `route` forces one configured route (used by reader comparisons)."""
    from lark.llm import roles

    return roles.call(ctx, role, req, route=route)
