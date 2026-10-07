"""Completed daily data and benchmark-observed trading sessions."""

from datetime import date, datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

from hedge_fund.data.protocol import DataClient

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
# Compatibility for callers that imported the old constant. All market-date
# calculations now follow B3's local timezone, including the disk cache.
NEW_YORK = SAO_PAULO


def completed_through() -> str:
    """Exclude the current São Paulo date, even after market close.

    Sessions and holidays are subsequently derived from observed benchmark
    bars; this cutoff never manufactures an exchange calendar.
    """
    return (datetime.now(SAO_PAULO).date() - timedelta(days=1)).isoformat()


def previous_day(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def session_closes(data: DataClient, benchmark: str, start: str, end: str) -> dict[str, float]:
    end = min(end, completed_through())
    if start > end:
        return {}
    bars = data.get_prices(benchmark, start, end)
    closes = {bar.time[:10]: bar.close for bar in bars if start <= bar.time[:10] <= end}
    for day, close in closes.items():
        if not isfinite(close) or close <= 0:
            raise ValueError(f"{benchmark}: close on {day} must be finite and positive")
    return dict(sorted(closes.items()))
