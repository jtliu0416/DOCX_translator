"""Application-local timestamp helpers."""

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import APP_TIMEZONE


LOCAL_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@lru_cache(maxsize=1)
def app_timezone():
    try:
        return ZoneInfo(APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        if APP_TIMEZONE == "Asia/Shanghai":
            return timezone(timedelta(hours=8), "Asia/Shanghai")
        raise


def local_now() -> datetime:
    return datetime.now(app_timezone())


def format_local_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=app_timezone())
    else:
        value = value.astimezone(app_timezone())
    return value.strftime(LOCAL_TIME_FORMAT)


def local_now_string() -> str:
    return format_local_time(local_now())


def local_expiry_string(days: int) -> str:
    return format_local_time(local_now() + timedelta(days=days))


def local_cutoff_string(days: int) -> str:
    return format_local_time(local_now() - timedelta(days=days))


def utc_timestamp_to_local_string(value: object) -> str | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return text
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return text

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return format_local_time(parsed)
