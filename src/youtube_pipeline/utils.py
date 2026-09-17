from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Iterator, TypeVar

T = TypeVar("T")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def chunks(values: list[T], size: int) -> Iterator[list[T]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_windows(start_date: str | None, end_date: str | None, window_days: int) -> list[tuple[str | None, str | None]]:
    """Datas do YAML são inclusivas; publishedBefore da API é tratado como limite exclusivo."""
    if not start_date and not end_date:
        return [(None, None)]
    if not start_date or not end_date:
        raise ValueError("Informe search.start_date e search.end_date juntos, ou omita ambos.")

    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_inclusive = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_inclusive < start:
        raise ValueError("search.end_date não pode ser anterior a search.start_date.")

    end_exclusive = end_inclusive + timedelta(days=1)
    result: list[tuple[str, str]] = []
    current = start
    while current < end_exclusive:
        nxt = min(current + timedelta(days=window_days), end_exclusive)
        result.append((_iso_z(current), _iso_z(nxt)))
        current = nxt
    return result


def stable_id(*parts: object) -> str:
    raw = "|".join("" if x is None else str(x) for x in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def parse_iso8601_duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    pattern = re.compile(
        r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
    )
    m = pattern.match(value)
    if not m:
        return None
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def safe_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def unique_nonempty(values: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))
