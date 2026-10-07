"""CachedDataClient tests — counting fake, no network."""

from hedge_fund.data.cached import CachedDataClient
from hedge_fund.data.models import CompanyFacts, Price


class CountingClient:
    """Counts calls; returns canned data."""

    def __init__(self, facts=None):
        self.calls = 0
        self._facts = facts

    def get_prices(self, ticker, start_date, end_date, interval="day", interval_multiplier=1):
        self.calls += 1
        return [Price(open=1.0, close=2.0, high=2.0, low=1.0, volume=100,
                      time=f"{start_date}T00:00:00Z")]

    def get_company_facts(self, ticker):
        self.calls += 1
        return self._facts

    def get_market_cap(self, ticker, end_date):
        self.calls += 1
        return 3.0e12


def test_cache_hit_skips_wrapped_client(tmp_path):
    inner = CountingClient()
    fd = CachedDataClient(inner, cache_dir=tmp_path)

    first = fd.get_prices("AAPL", "2024-01-01", "2024-12-31")
    second = fd.get_prices("AAPL", "2024-01-01", "2024-12-31")

    assert inner.calls == 1
    assert isinstance(second[0], Price)  # re-hydrated to the pydantic model
    assert second[0].close == first[0].close


def test_valid_json_with_invalid_cache_shape_is_a_miss(tmp_path):
    inner = CountingClient(facts=CompanyFacts(ticker='PETR4', name='Synthetic'))
    adapter = CachedDataClient(inner, cache_dir=tmp_path)
    key = adapter._key('get_company_facts', {'ticker': 'PETR4'})
    (tmp_path / f'{key}.json').write_text('[]')
    assert adapter.get_company_facts('PETR4').name == 'Synthetic'
    assert adapter.get_company_facts('PETR4').name == 'Synthetic'
    assert inner.calls == 1


def test_different_params_different_entries(tmp_path):
    inner = CountingClient()
    fd = CachedDataClient(inner, cache_dir=tmp_path)

    fd.get_prices("AAPL", "2024-01-01", "2024-12-31")
    fd.get_prices("AAPL", "2024-01-01", "2025-12-31")  # different end date

    assert inner.calls == 2


def test_refresh_busts_cache(tmp_path):
    inner = CountingClient()
    CachedDataClient(inner, cache_dir=tmp_path).get_prices("AAPL", "2024-01-01", "2024-12-31")
    CachedDataClient(inner, cache_dir=tmp_path, refresh=True).get_prices(
        "AAPL", "2024-01-01", "2024-12-31")

    assert inner.calls == 2


def test_none_item_is_cached(tmp_path):
    """A cached None (ticker without facts) must not re-hit the API."""
    inner = CountingClient(facts=None)
    fd = CachedDataClient(inner, cache_dir=tmp_path)

    assert fd.get_company_facts("ZZZZ") is None
    assert fd.get_company_facts("ZZZZ") is None
    assert inner.calls == 1


def test_item_rehydrates(tmp_path):
    inner = CountingClient(facts=CompanyFacts(ticker="AAPL", sector="Tech"))
    fd = CachedDataClient(inner, cache_dir=tmp_path)

    fd.get_company_facts("AAPL")
    facts = fd.get_company_facts("AAPL")

    assert inner.calls == 1
    assert isinstance(facts, CompanyFacts)
    assert facts.sector == "Tech"


def test_scalar_cached(tmp_path):
    inner = CountingClient()
    fd = CachedDataClient(inner, cache_dir=tmp_path)

    assert fd.get_market_cap("AAPL", "2024-12-31") == 3.0e12
    assert fd.get_market_cap("AAPL", "2024-12-31") == 3.0e12
    assert inner.calls == 1


def test_daily_price_entries_without_fetch_metadata_are_refreshed(tmp_path):
    import json
    inner = CountingClient()
    fd = CachedDataClient(inner, cache_dir=tmp_path)
    fd.get_prices("AAPL", "2024-01-01", "2024-01-01")
    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_text())
    payload.pop("fetched_at")
    path.write_text(json.dumps(payload))
    fd.get_prices("AAPL", "2024-01-01", "2024-01-01")
    assert inner.calls == 2


def test_incomplete_daily_price_cache_is_refreshed_after_date_ends(tmp_path, monkeypatch):
    from datetime import datetime
    from hedge_fund.data import cached
    class Clock:
        day = 1
        @classmethod
        def now(cls, tz):
            return datetime(2024, 1, cls.day, 23, 0, tzinfo=tz)
    monkeypatch.setattr(cached, "datetime", Clock)
    # Parsing remains the standard library's responsibility.
    Clock.fromisoformat = datetime.fromisoformat
    inner = CountingClient()
    fd = CachedDataClient(inner, cache_dir=tmp_path)
    fd.get_prices("AAPL", "2024-01-01", "2024-01-01")
    fd.get_prices("AAPL", "2024-01-01", "2024-01-01")
    assert inner.calls == 2
    Clock.day = 2
    fd.get_prices("AAPL", "2024-01-01", "2024-01-01")
    assert inner.calls == 3
    fd.get_prices("AAPL", "2024-01-01", "2024-01-01")
    assert inner.calls == 3


def test_provider_namespaces_never_reuse_other_sources(tmp_path):
    """Identical parameters across providers must fetch independently."""
    first, second = CountingClient(), CountingClient()
    first.cache_namespace = "synthetic-original-provider-v1"
    second.cache_namespace = "openmarkets-rest-synthetic-v1-adjusted"
    original = CachedDataClient(first, cache_dir=tmp_path)
    migrated = CachedDataClient(second, cache_dir=tmp_path)
    original.get_market_cap("PETR4", "2024-12-31")
    migrated.get_market_cap("PETR4", "2024-12-31")
    migrated.get_market_cap("PETR4", "2024-12-31")
    assert first.calls == second.calls == 1
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_expired_fundamental_entry_is_refetched(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from hedge_fund.data import cached

    class Clock:
        elapsed = 0
        fromisoformat = datetime.fromisoformat

        @classmethod
        def now(cls, tz):
            return datetime(2025, 1, 1, tzinfo=tz) + timedelta(seconds=cls.elapsed)

    monkeypatch.setattr(cached, "datetime", Clock)
    inner = CountingClient(facts=CompanyFacts(ticker="PETR4", name="Synthetic"))
    adapter = CachedDataClient(inner, cache_dir=tmp_path, ttl_seconds=60)
    adapter.get_company_facts("PETR4")
    Clock.elapsed = 59
    adapter.get_company_facts("PETR4")
    assert inner.calls == 1
    Clock.elapsed = 61
    adapter.get_company_facts("PETR4")
    assert inner.calls == 2


def test_market_data_failure_is_never_cached(tmp_path):
    import pytest
    from hedge_fund.data.client import CoverageError
    inner = CountingClient()

    def unavailable(*args):
        raise CoverageError("synthetic uncovered capability")

    inner.get_prices = unavailable
    with pytest.raises(CoverageError):
        CachedDataClient(inner, cache_dir=tmp_path).get_prices("PETR4", "2024-01-01", "2024-12-31")
    assert not list(tmp_path.glob("*.json"))


def test_completed_price_range_serves_replay_slices_without_extra_api_calls(tmp_path):
    class History(CountingClient):
        def get_prices(self, ticker, start_date, end_date, interval='day', interval_multiplier=1):
            self.calls += 1
            return [Price(open=30, high=31, low=29, close=30, volume=100, time=day)
                    for day in ('2024-01-02', '2024-01-03', '2024-01-04')]
    inner = History()
    data = CachedDataClient(inner, cache_dir=tmp_path)
    data.get_prices('PETR4', '2024-01-01', '2024-01-31')
    assert [p.time for p in data.get_prices('PETR4', '2024-01-03', '2024-01-04')] == ['2024-01-03', '2024-01-04']
    assert data.get_prices('PETR4', '2024-01-06', '2024-01-06') == []
    assert inner.calls == 1


def test_exploratory_full_metrics_history_reuses_earlier_cutoff_without_future_rows(tmp_path):
    from hedge_fund.data.models import FinancialMetrics
    class History:
        point_in_time = False
        calls = 0
        def get_financial_metrics(self, ticker, end_date, period='ttm', limit=10):
            self.calls += 1
            return [FinancialMetrics(ticker=ticker, report_period=day, period=period,
                    point_in_time=False) for day in ('2024-06-30', '2024-03-31', '2023-12-31')
                    if day <= end_date][:limit]
    inner = History()
    data = CachedDataClient(inner, cache_dir=tmp_path)
    data.get_financial_metrics('PETR4', '2024-07-01', limit=10000)
    rows = data.get_financial_metrics('PETR4', '2024-04-01', limit=1)
    assert [r.report_period for r in rows] == ['2024-03-31']
    assert rows[0].point_in_time is False
    assert inner.calls == 1


def test_point_in_time_provider_cannot_reuse_later_cutoff_metrics(tmp_path):
    from hedge_fund.data.models import FinancialMetrics
    class History:
        point_in_time = True
        calls = 0
        def get_financial_metrics(self, ticker, end_date, period='ttm', limit=10):
            self.calls += 1
            return [FinancialMetrics(ticker=ticker, report_period='2023-12-31', period=period,
                    filing_date=end_date, market_cap=self.calls * 100)]
    inner = History()
    data = CachedDataClient(inner, cache_dir=tmp_path)
    data.get_financial_metrics('PETR4', '2024-07-01', limit=10000)
    assert data.get_financial_metrics('PETR4', '2024-04-01', limit=1)[0].market_cap == 200
    assert inner.calls == 2


def test_prefetched_exploratory_metrics_cannot_expose_known_future_delivery(tmp_path):
    from hedge_fund.data.models import FinancialMetrics
    class History:
        point_in_time = False
        def get_financial_metrics(self, ticker, end_date, period='ttm', limit=10):
            return [FinancialMetrics(ticker=ticker, period=period, point_in_time=False,
                    report_period='2024-03-31', filing_date='2024-05-13', market_cap=10),
                    FinancialMetrics(ticker=ticker, period=period, point_in_time=False,
                    report_period='2023-12-31', filing_date='2024-03-08', market_cap=9)]
    data = CachedDataClient(History(), cache_dir=tmp_path)
    data.get_financial_metrics('PETR4', '2024-06-14', limit=10000)
    assert [r.report_period for r in data.get_financial_metrics('PETR4', '2024-04-01')] == ['2023-12-31']
    assert len(data.get_financial_metrics('PETR4', '2024-05-13')) == 2
