"""Disk-cached DataClient wrapper.

Wraps any DataClient with a JSON file cache under ~/.hedge-fund/cache/data/. A warm
cache reuses completed historical responses. Daily prices are reusable only
when fetched after the requested end date has ended in São Paulo.

    fd = CachedDataClient(FDClient())
    prices = fd.get_prices("AAPL", "2024-01-01", "2024-12-31")  # API call
    prices = fd.get_prices("AAPL", "2024-01-01", "2024-12-31")  # disk, ~0ms

Failure semantics are inherited: only successful responses are cached, and
errors from the wrapped client propagate (fail-loud preserved). Pass
refresh=True to ignore existing entries (they are rewritten on fetch).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from hedge_fund.data.models import (
    CompanyFacts,
    CompanyNews,
    Earnings,
    EarningsRecord,
    FinancialMetrics,
    InsiderTrade,
    Price,
)
from hedge_fund.data.protocol import DataClient
from hedge_fund.data.sessions import SAO_PAULO
from hedge_fund.paths import CACHE_DIR

DEFAULT_CACHE_DIR = CACHE_DIR / "data"


class CachedDataClient:
    """DataClient that memoizes another DataClient's responses on disk."""

    def __init__(
        self,
        client: DataClient,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        refresh: bool = False,
        ttl_seconds: float = 86400,
    ) -> None:
        self._client = client
        self._dir = Path(cache_dir)
        self._refresh = refresh
        self._ttl = ttl_seconds
        self.source = getattr(client, "source", "unspecified")
        self.provider = getattr(client, "provider", self.source)
        self.currency = getattr(client, "currency", "BRL")
        self.point_in_time = getattr(client, "point_in_time", True)
        self.earnings_surprises = getattr(client, "earnings_surprises", True)
        self.price_basis = getattr(client, "price_basis", None)
        self.cache_namespace = getattr(client, "cache_namespace", type(client).__module__ + "." + type(client).__qualname__)

    # ------------------------------------------------------------------
    # DataClient protocol
    # ------------------------------------------------------------------

    def get_prices(self, ticker, start_date, end_date, interval="day", interval_multiplier=1):
        return self._cached_list(
            "get_prices", Price,
            {"ticker": ticker, "start_date": start_date, "end_date": end_date,
             "interval": interval, "interval_multiplier": interval_multiplier},
            lambda: self._client.get_prices(
                ticker, start_date, end_date, interval, interval_multiplier),
        )

    def get_financial_metrics(self, ticker, end_date, period="ttm", limit=10):
        return self._cached_list(
            "get_financial_metrics", FinancialMetrics,
            {"ticker": ticker, "end_date": end_date, "period": period, "limit": limit},
            lambda: self._client.get_financial_metrics(ticker, end_date, period, limit),
        )

    def get_news(self, ticker, end_date, start_date=None, limit=1000):
        return self._cached_list(
            "get_news", CompanyNews,
            {"ticker": ticker, "end_date": end_date, "start_date": start_date, "limit": limit},
            lambda: self._client.get_news(ticker, end_date, start_date, limit),
        )

    def get_insider_trades(self, ticker, end_date, start_date=None, limit=1000):
        return self._cached_list(
            "get_insider_trades", InsiderTrade,
            {"ticker": ticker, "end_date": end_date, "start_date": start_date, "limit": limit},
            lambda: self._client.get_insider_trades(ticker, end_date, start_date, limit),
        )

    def get_earnings_history(self, ticker, limit=12):
        return self._cached_list(
            "get_earnings_history", EarningsRecord,
            {"ticker": ticker, "limit": limit},
            lambda: self._client.get_earnings_history(ticker, limit),
        )

    def get_company_facts(self, ticker):
        return self._cached_item(
            "get_company_facts", CompanyFacts, {"ticker": ticker},
            lambda: self._client.get_company_facts(ticker),
        )

    def get_earnings(self, ticker):
        return self._cached_item(
            "get_earnings", Earnings, {"ticker": ticker},
            lambda: self._client.get_earnings(ticker),
        )

    def get_market_cap(self, ticker, end_date):
        return self._cached_scalar(
            "get_market_cap", {"ticker": ticker, "end_date": end_date},
            lambda: self._client.get_market_cap(ticker, end_date),
        )

    # ------------------------------------------------------------------
    # Cache mechanics
    # ------------------------------------------------------------------

    def _key(self, method: str, params: dict) -> str:
        canonical = json.dumps(params, sort_keys=True)
        return hashlib.sha256(f"{self.cache_namespace}|{method}|{canonical}".encode()).hexdigest()[:24]

    def _read(self, key: str) -> dict | None:
        if self._refresh:
            return None
        path = self._dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            hit = json.loads(path.read_text())
            if not isinstance(hit, dict) or 'data' not in hit:
                return None
            if hit.get("namespace") != self.cache_namespace:
                return None
            fetched = datetime.fromisoformat(hit["fetched_at"])
            if fetched.tzinfo is None or datetime.now(SAO_PAULO) - fetched > timedelta(seconds=self._ttl):
                return None
            return hit
        except (json.JSONDecodeError, OSError, ValueError, KeyError, TypeError):
            return None  # corrupt entry -> miss; rewritten on fetch

    def _write(self, key: str, payload: dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {**payload, "namespace": self.cache_namespace,
                   "fetched_at": payload.get("fetched_at", datetime.now(SAO_PAULO).isoformat())}
        # Unique temporary files prevent concurrent warmers from publishing
        # partial JSON or clobbering another writer's temporary file.
        with tempfile.NamedTemporaryFile(mode="w", dir=self._dir, delete=False) as f:
            json.dump(payload, f)
            temporary = f.name
        os.replace(temporary, self._dir / f"{key}.json")

    def prefetch_history(self, tickers: list[str], benchmark: str, start: str, end: str,
                         *, fundamentals: bool = True) -> None:
        """Fetch complete ranges once; replay cutoffs reuse contained slices.

        Fundamentals reuse is limited to explicitly non-PIT providers. It
        retains their restated/exploratory semantics, never invents vintages.
        """
        from datetime import date
        lookback = (date.fromisoformat(start) - timedelta(days=7)).isoformat()
        for ticker in dict.fromkeys([*tickers, benchmark]):
            self.get_prices(ticker, lookback, end)
            if fundamentals and ticker in tickers:
                self.get_company_facts(ticker)
                self.get_financial_metrics(ticker, end, limit=10000)

    def _contained(self, method: str, params: dict) -> dict | None:
        if self._refresh or method not in {"get_prices", "get_financial_metrics"}:
            return None
        if method == "get_financial_metrics" and self.point_in_time:
            return None
        for path in sorted(self._dir.glob("*.json")):
            entry = self._read(path.stem)
            if not entry or entry.get("method") != method:
                continue
            stored = entry.get("params", {})
            if stored.get("ticker") != params["ticker"]:
                continue
            if stored.get("end_date", "") < params["end_date"]:
                continue
            if method == "get_prices":
                if stored.get("start_date", "9999") > params["start_date"] or any(
                    stored.get(k) != params[k] for k in ("interval", "interval_multiplier")
                ):
                    continue
                fetched = datetime.fromisoformat(entry["fetched_at"])
                if fetched.astimezone(SAO_PAULO).date().isoformat() <= params["end_date"]:
                    continue
                return {**entry, "data": [r for r in entry["data"]
                        if params["start_date"] <= r["time"][:10] <= params["end_date"]]}
            if stored.get("period") == params["period"] and stored.get("limit", 0) >= 10000:
                # A future prefetch must respect known delivery dates even
                # though retained revisions remain explicitly exploratory.
                # Older revisions of a withheld row are not reconstructible
                # from this cache; omit it instead of exposing future facts.
                rows = [r for r in entry["data"] if r["report_period"] <= params["end_date"]
                        and (not r.get("filing_date") or r["filing_date"] <= params["end_date"])]
                rows.sort(key=lambda r: r["report_period"], reverse=True)
                return {**entry, "data": rows[:params["limit"]]}
        return None

    def _cached_list(self, method: str, model_cls, params: dict, fetch: Callable) -> list:
        key = self._key(method, params)
        hit = self._read(key)
        if hit is not None and method == "get_prices" and params.get("interval") == "day":
            try:
                fetched = datetime.fromisoformat(hit["fetched_at"])
                complete = fetched.tzinfo is not None and (
                    fetched.astimezone(SAO_PAULO).date().isoformat() > params["end_date"]
                )
            except (KeyError, TypeError, ValueError):
                complete = False
            if not complete:
                hit = None
        if hit is None:
            hit = self._contained(method, params)
        if hit is not None:
            return [model_cls(**row) for row in hit["data"]]
        fetched_at = datetime.now(SAO_PAULO).isoformat()
        result = fetch()
        payload = {"method": method, "params": params, "data": [r.model_dump() for r in result]}
        if method == "get_prices":
            payload["fetched_at"] = fetched_at
        self._write(key, payload)
        return result

    def _cached_item(self, method: str, model_cls, params: dict, fetch: Callable):
        key = self._key(method, params)
        hit = self._read(key)
        if hit is not None:
            return model_cls(**hit["data"]) if hit["data"] is not None else None
        result = fetch()
        self._write(key, {"data": result.model_dump() if result is not None else None})
        return result

    def _cached_scalar(self, method: str, params: dict, fetch: Callable):
        key = self._key(method, params)
        hit = self._read(key)
        if hit is not None:
            return hit["data"]
        result = fetch()
        self._write(key, {"data": result})
        return result
