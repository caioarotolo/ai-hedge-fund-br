"""Opt-in live OpenMarkets coverage checks; no legacy-provider requests.

Enable deliberately with OPENMARKETS_LIVE_TESTS=1 and a locally configured
OPENMARKETS_API_KEY. These checks require an account with the requested access.
Passing them confirms execution/coverage, not historical investment validity.
"""
import os

import pytest

from hedge_fund.data.client import OpenMarketsClient

TICKERS = ["PETR4", "VALE3", "ITUB4"]
PRICE_START = "2025-01-02"
PRICE_END = "2025-01-31"

pytestmark = pytest.mark.skipif(
    os.environ.get("OPENMARKETS_LIVE_TESTS") != "1" or not os.environ.get("OPENMARKETS_API_KEY"),
    reason="opt-in live tests require OPENMARKETS_LIVE_TESTS=1 and OPENMARKETS_API_KEY",
)


@pytest.fixture(scope="module")
def om():
    with OpenMarketsClient() as adapter:
        yield adapter


@pytest.mark.parametrize("ticker", TICKERS)
def test_prices(om, ticker):
    bars = om.get_prices(ticker, PRICE_START, PRICE_END)
    assert bars, f"No daily OHLCV for {ticker}"
    assert all(bar.currency == "BRL" and bar.data_source == "openmarkets" for bar in bars)
    assert all(bar.price_basis == "adjusted" and bar.close > 0 for bar in bars)
    assert [bar.time for bar in bars] == sorted({bar.time for bar in bars})


@pytest.mark.parametrize("ticker", TICKERS)
def test_financial_metrics(om, ticker):
    rows = om.get_financial_metrics(ticker, PRICE_END, period="ttm", limit=4)
    assert rows, f"No historical mapped metrics for {ticker}"
    assert all(row.report_period <= PRICE_END for row in rows)
    assert all(row.currency == "BRL" and row.data_source == "openmarkets" for row in rows)
    assert all(row.point_in_time is False and row.filing_date
               and row.filing_date <= PRICE_END for row in rows)
    assert any(row.market_cap is not None or row.price_to_earnings_ratio is not None for row in rows)


@pytest.mark.parametrize("ticker", TICKERS)
def test_company_facts(om, ticker):
    facts = om.get_company_facts(ticker)
    assert facts is not None and facts.name and facts.exchange == "B3"


@pytest.mark.parametrize("ticker", TICKERS)
def test_coverage(om, ticker):
    coverage = om.get_coverage(ticker)
    assert coverage["data"], f"Empty coverage report for {ticker}"
