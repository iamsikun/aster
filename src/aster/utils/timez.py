import os
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo


def tz() -> tzinfo:
    """ASTER_TZ (set from the config's `timezone`), else the system time zone."""
    name = os.getenv("ASTER_TZ")
    if name:
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo or UTC


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_local() -> datetime:
    return datetime.now(tz())


def today_str() -> str:
    return now_local().strftime("%Y-%m-%d")


def iso_now() -> str:
    return now_utc().isoformat(timespec="seconds")
