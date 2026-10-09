"""Cash and capacity accounting, enforced before dispatch.

A model call first `reserve`s its estimated cash and one unit of route capacity. The
reservation is later `settle`d with the actual cost, or `release`d if the call never
ran. Until then it counts at its estimate, so concurrent workers cannot spend the
same remaining balance. Entries of one reservation share an id prefix:
    <rid>          reserve
    <rid>:settle   settle (actual cash replaces the estimate)
    <rid>:release  release (the reservation no longer counts)

Cash caps (config `budget.*`):
- api_credit routes: `daily_cash_usd` and `monthly_cash_usd`.
- api_paid routes: `paid_fallback_usd` per month (0 by default = disabled).
- subscription / local / none routes cost no cash; they are limited by the
  route's `daily_calls` capacity instead.
"""

from __future__ import annotations

import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, time

from lark.context import RunContext
from lark.records import LedgerEntry
from lark.utils.timez import now_local, tz

_LOCK = threading.RLock()
CASH_BILLING = ("api_credit", "api_paid")


def period_starts() -> tuple[str, str]:
    """UTC ISO timestamps for the start of today and of this month in the local timezone."""
    now = now_local()
    today = datetime.combine(now.date(), time.min, tzinfo=tz()).astimezone(UTC)
    month = datetime.combine(now.date().replace(day=1), time.min, tzinfo=tz()).astimezone(UTC)
    return today.isoformat(timespec="microseconds"), month.isoformat(timespec="microseconds")


def _after(a: str, b: str) -> bool:
    return datetime.fromisoformat(a) >= datetime.fromisoformat(b)


@dataclass
class Totals:
    cash_today: float = 0.0
    cash_month: float = 0.0
    cash_month_credit: float = 0.0
    cash_month_paid: float = 0.0
    calls_today: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    events_today: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))


def _totals(ctx: RunContext) -> Totals:
    today_ts, month_ts = period_starts()
    groups: dict[str, dict[str, LedgerEntry]] = defaultdict(dict)
    t = Totals()
    for e in ctx.store.all(LedgerEntry, "ts >= ?", (month_ts,), order="seq"):
        if e.event in ("rate_limited", "auth_failed"):
            if _after(e.at, today_ts):
                t.events_today[e.route].append(e.event)
            continue
        base, _, _ = e.entry_id.partition(":")
        groups[base][e.event] = e
    for g in groups.values():
        res = g.get("reserve")
        if res is None or "release" in g:
            continue
        cash = g["settle"].cash_usd if "settle" in g else res.cash_usd
        t.cash_month += cash
        if res.billing == "api_credit":
            t.cash_month_credit += cash
        elif res.billing == "api_paid":
            t.cash_month_paid += cash
        if _after(res.at, today_ts):
            t.cash_today += cash
            t.calls_today[res.route] += res.calls
    return t


def reserve(ctx: RunContext, route_name: str, route_cfg: dict, est_cash: float, job_id: str | None = None,
            *, provider: str = "", model: str | None = None) -> str | None:
    """Reserve cash + one call of capacity. Returns a reservation id, or None if any cap would be exceeded."""
    billing = route_cfg.get("billing", "none")
    est_cash = float(est_cash) if billing in CASH_BILLING else 0.0
    with _LOCK:
        t = _totals(ctx)
        cap_calls = route_cfg.get("daily_calls")
        if cap_calls is not None and t.calls_today[route_name] + 1 > int(cap_calls):
            return None
        if billing == "api_credit":
            if t.cash_today + est_cash > float(ctx.config.get("budget.daily_cash_usd", 0.5)) + 1e-12:
                return None
            if t.cash_month_credit + est_cash > float(ctx.config.get("budget.monthly_cash_usd", 10.0)) + 1e-12:
                return None
        elif billing == "api_paid":
            if t.cash_month_paid + est_cash > float(ctx.config.get("budget.paid_fallback_usd", 0.0)) + 1e-12:
                return None
        rid = f"r_{uuid.uuid4().hex[:16]}"
        ctx.store.append(LedgerEntry(
            entry_id=rid, job_id=job_id, route=route_name, provider=provider or route_cfg.get("worker", ""),
            model=model or route_cfg.get("model"), billing=billing, event="reserve", cash_usd=est_cash, calls=1,
        ))
        return rid


def _reservation(ctx: RunContext, rid: str) -> LedgerEntry | None:
    rows = ctx.store.all(LedgerEntry, "json_extract(json, '$.entry_id') = ?", (rid,))
    return rows[0] if rows else None


def settle(ctx: RunContext, reservation_id: str, actual_cash: float, usage: dict | None = None) -> None:
    usage = usage or {}
    res = _reservation(ctx, reservation_id)
    if res is None:
        return
    cash = float(actual_cash) if res.billing in CASH_BILLING else 0.0
    with _LOCK:
        ctx.store.append(LedgerEntry(
            entry_id=f"{reservation_id}:settle", job_id=res.job_id, route=res.route, provider=res.provider,
            model=res.model, billing=res.billing, event="settle", cash_usd=cash, calls=res.calls,
            tokens_in=int(usage.get("input_tokens", 0) or 0), tokens_out=int(usage.get("output_tokens", 0) or 0),
        ))


def release(ctx: RunContext, reservation_id: str, note: str | None = None) -> None:
    """Cancel a reservation whose call never ran (or failed before spending)."""
    res = _reservation(ctx, reservation_id)
    if res is None:
        return
    with _LOCK:
        ctx.store.append(LedgerEntry(
            entry_id=f"{reservation_id}:release", job_id=res.job_id, route=res.route, provider=res.provider,
            model=res.model, billing=res.billing, event="release", cash_usd=-res.cash_usd, note=note,
        ))


def record_event(ctx: RunContext, route: str, event: str, note: str | None = None, *, provider: str = "",
                 billing: str = "none", model: str | None = None) -> None:
    """Record rate_limited / auth_failed events (no cash, no capacity)."""
    ctx.store.append(LedgerEntry(
        entry_id=f"ev_{uuid.uuid4().hex[:16]}", route=route, provider=provider, model=model, billing=billing,
        event=event, note=(note or "")[:300],  # type: ignore[arg-type]
    ))


def calls_today(ctx: RunContext) -> dict[str, int]:
    return dict(_totals(ctx).calls_today)


def summary(ctx: RunContext) -> dict:
    t = _totals(ctx)
    return {
        "cash_today_usd": round(t.cash_today, 4),
        "cash_month_usd": round(t.cash_month, 4),
        "monthly_cap_usd": float(ctx.config.get("budget.monthly_cash_usd", 10.0)),
        "calls_today": dict(t.calls_today),
        "events_today": {k: list(v) for k, v in t.events_today.items()},
    }
