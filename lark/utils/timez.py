import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("LARK_TZ", "America/Chicago"))


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_local() -> datetime:
    return datetime.now(tz())


def today_str() -> str:
    return now_local().strftime("%Y-%m-%d")


def days_ago(n: int) -> datetime:
    return now_local() - timedelta(days=n)


def iso_now() -> str:
    return now_utc().isoformat(timespec="seconds")
