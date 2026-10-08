"""OpenMarkets adapter contracts using synthetic HTTP fixtures, never live data.

These tests prove parsing and refusal behavior. They do not certify the real
provider's schema, adjustments, coverage, or point-in-time availability.
"""
import pytest
import requests

from hedge_fund.data.client import CoverageError, OpenMarketsClient, OpenMarketsError


class SyntheticResponse:
    def __init__(self, status=200, payload=None, text="", headers=None):
        self.status_code = status
        self.payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("hedge_fund.data.client.time.sleep", lambda seconds: None)
    with OpenMarketsClient(api_key="synthetic-secret-key", request_interval=0) as adapter:
        yield adapter


def stub(client, *responses):
    remaining = list(responses)
    calls = []

    def request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        response = remaining.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    client._session.request = request
    return calls


def response(data, cursor=None):
    return SyntheticResponse(payload={"data": data, "meta": {"next_cursor": cursor}})


def price(day, **overrides):
    row = dict(date=f"2024-01-{day:02d}", open=30.0, high=32.0,
               low=29.0, close=31.0, volume=100, currency="BRL",
               price_basis="adjusted")
    return {**row, **overrides}


def metric(key, value, unit, report="2024-03-31", *, delivery=None, version=1, period="ltm", **kwargs):
    if unit in {"BRL", "BRL thousands", "BRL_thousands", "mil BRL", "BRL millions", "BRL_millions"}:
        output_unit = "currency"
    elif unit in {"BRL/share", "BRL/ação"}:
        output_unit = "currency_per_share"
    elif unit in {"ratio", "fraction", "decimal", "dimensionless", "1", "%", "percent", "percentage", "pct", "multiple", "x", "times"}:
        output_unit = "ratio"
    else:
        output_unit = None
    row = dict(
        metric_value_key=key,
        metric_name="ltm" if ":" in key else key,
        base_metric_name=key.split(":", 1)[1] if ":" in key else None,
        metric_group="synthetic",
        source_dataset="synthetic-test-fixture",
        period_kind="quarterly",
        statement_type="consolidated_income_statement",
        consolidation="consolidado",
        period_start="2024-01-01",
        period_end=report,
        reference_date=report,
        version=version,
        source_document_id="synthetic-document",
        currency="REAL",
        source_delivery_date=delivery or report,
        metric_key=key,
        date=report,
        value=value,
        unit=unit,
        output_unit=output_unit,
        calculation_status="calculated" if value is not None else "missing_inputs",
        available=value is not None,
        availability="available" if value is not None else "missing_inputs",
        value_scale=None,
        currency_scale=None,
        scale_status="metric_inputs" if output_unit == "currency" else "not_applicable",
        derived=False,
        source="financial.metric_values",
        period=period,
    )
    row.update(kwargs)
    return row


def test_daily_b3_query_normalization_bearer_and_source(client):
    calls = stub(client, response([price(2)]))
    bars = client.get_prices(" petr4.sa ", "2024-01-01", "2024-01-31")
    assert calls[0]["url"] == "https://api.openmarkets.com.br/v1/quotes/PETR4"
    assert calls[0]["params"] == dict(start_date="2024-01-01", end_date="2024-01-31",
        latest_only="false", frequency="daily", price_basis="adjusted", limit=1000)
    assert calls[0]["allow_redirects"] is False
    assert client._session.headers["Authorization"] == "Bearer synthetic-secret-key"
    assert "X-API-KEY" not in client._session.headers
    assert bars[0].currency == "BRL" and bars[0].data_source == "openmarkets"
    assert bars[0].price_basis == "adjusted" and bars[0].time == "2024-01-02"


def test_cursor_pages_keep_same_openmarkets_host_sort_deduplicate_and_clip(client):
    calls = stub(client, response([price(3), price(2)], "opaque-cursor"),
                 response([price(2), price(1), price(4)]))
    bars = client.get_prices("PETR4", "2024-01-02", "2024-01-03")
    assert [bar.time for bar in bars] == ["2024-01-02", "2024-01-03"]
    assert calls[1]["url"] == calls[0]["url"]
    assert calls[1]["params"]["cursor"] == "opaque-cursor"


def test_repeated_cursor_is_a_failure(client):
    stub(client, response([price(1)], "repeated"), response([price(2)], "repeated"))
    with pytest.raises(OpenMarketsError, match="repeated pagination cursor"):
        client.get_prices("PETR4", "2024-01-01", "2024-01-31")


def test_truncated_page_without_cursor_cannot_pass_as_complete_history(client):
    stub(client, SyntheticResponse(payload={'data': [price(2)], 'meta': {'truncated': True}}))
    with pytest.raises(OpenMarketsError, match='truncated page'):
        client.get_prices('PETR4', '2024-01-01', '2024-01-31')


@pytest.mark.parametrize("status", [301, 401, 403, 404, 500])
def test_http_errors_never_become_empty_data_or_expose_credentials(client, status):
    stub(client, SyntheticResponse(status, text="synthetic-secret-key"))
    with pytest.raises(OpenMarketsError) as error:
        client.get_prices("PETR4", "2024-01-01", "2024-01-31")
    assert error.value.status_code == status
    assert "synthetic-secret-key" not in str(error.value)


def test_transport_errors_are_redacted(client):
    stub(client, requests.ConnectionError("Authorization: Bearer synthetic-secret-key"))
    with pytest.raises(OpenMarketsError, match="transport failure") as error:
        client.get_prices("PETR4", "2024-01-01", "2024-01-31")
    assert "synthetic-secret-key" not in str(error.value)


def test_page_failure_refuses_partial_price_history(client):
    stub(client, response([price(1)], "second"), SyntheticResponse(500))
    with pytest.raises(OpenMarketsError):
        client.get_prices("PETR4", "2024-01-01", "2024-01-31")


def test_429_retries_and_can_recover(client):
    calls = stub(client, SyntheticResponse(429, headers={"Retry-After": "12"}), response([price(2)]))
    assert len(client.get_prices("PETR4", "2024-01-01", "2024-01-31")) == 1
    assert len(calls) == 2


def test_429_exhaustion_raises(client):
    stub(client, *[SyntheticResponse(429) for _ in range(len(client._RETRY_DELAYS) + 1)])
    with pytest.raises(OpenMarketsError) as error:
        client.get_prices("PETR4", "2024-01-01", "2024-01-31")
    assert error.value.status_code == 429


@pytest.mark.parametrize("payload", [[], {}, {"prices": []}, {"data": {}, "error": "bad"}])
def test_unknown_rest_envelope_fails_loudly(client, payload):
    stub(client, SyntheticResponse(payload=payload))
    with pytest.raises(OpenMarketsError):
        client.get_prices("PETR4", "2024-01-01", "2024-01-31")


def test_invalid_json_fails_loudly(client):
    stub(client, SyntheticResponse(payload=ValueError("synthetic-secret-key")))
    with pytest.raises(OpenMarketsError, match="invalid JSON"):
        client.get_prices("PETR4", "2024-01-01", "2024-01-31")


@pytest.mark.parametrize("rows", [[{"date": "2024-01-02", "close": 31}],
    [price(2, close=float("nan"))], [price(2, volume=1.5)], [price(2, low=35)],
    [price(2, currency="USD")], [price(2, price_basis="raw")],
    [price(2), price(2, close=30)], {"close": 31}])
def test_incomplete_wrong_basis_or_conflicting_prices_are_refused(client, rows):
    stub(client, response(rows))
    with pytest.raises(CoverageError):
        client.get_prices("PETR4", "2024-01-01", "2024-01-31")


@pytest.mark.parametrize("ticker", ["B3SA3", "AAPL34"])
def test_alphanumeric_roots_and_bdrs_reach_the_api(client, ticker):
    calls = stub(client, response([price(2)]))
    client.get_prices(ticker, "2024-01-01", "2024-01-31")
    assert calls[0]["url"] == f"https://api.openmarkets.com.br/v1/quotes/{ticker}"


@pytest.mark.parametrize("ticker", ["AAPL", "PETR4.SA.BAD", "../../PETR4", ""])
def test_foreign_or_invalid_tickers_are_refused_without_request(client, ticker):
    calls = stub(client)
    with pytest.raises(CoverageError):
        client.get_prices(ticker, "2024-01-01", "2024-01-31")
    assert calls == []


def test_non_daily_prices_are_explicitly_unsupported(client):
    with pytest.raises(CoverageError, match="daily"):
        client.get_prices("PETR4", "2024-01-01", "2024-01-31", interval="minute")


def test_financial_history_maps_real_keys_and_keeps_normalized_brl_values(client):
    calls = stub(client, response([
        metric("ltm:roe", 0.15, "ratio", delivery="2024-04-17"),
        metric("market_cap", 123_000_000, "BRL", delivery="2024-04-19",
               value_scale="normalized_from_thousands", currency_scale="MIL"),
        metric("ltm:net_margin", 0.12, "ratio", delivery="2024-04-18"),
        metric("not_mapped", 1, "BRL"),
        metric("pe_ratio", 7, "ratio", report="2023-12-31", delivery="2024-01-29"),
        metric("ltm:roe", 0.20, "ratio", report="2024-06-30", delivery="2024-07-29"),
    ]))
    rows = client.get_financial_metrics("PETR4", "2024-04-30", limit=1)
    assert len(rows) == 1 and rows[0].report_period == "2024-03-31"
    assert rows[0].return_on_equity == 0.15 and rows[0].net_margin == 0.12
    assert rows[0].market_cap == 123_000_000  # already normalized from MIL by OpenMarkets
    assert rows[0].price_to_earnings_ratio is None
    assert rows[0].currency == "BRL" and rows[0].data_source == "openmarkets"
    assert rows[0].point_in_time is False
    assert rows[0].filing_date == "2024-04-19" and rows[0].filing_datetime is None
    assert calls[0]["params"]["period"] == "ltm"
    assert calls[0]["params"]["mode"] == "history"
    assert calls[0]["params"]["end_date"] == "2024-04-30"
    assert calls[0]["params"]["market_date_mode"] == "statement_date"
    assert "metric_keys" not in calls[0]["params"]  # history exposes keys rejected by the API filter
    assert "filing_date_lte" not in calls[0]["params"]


def test_semantic_output_unit_fills_missing_physical_unit(client):
    row = metric("ltm:roe", 0.12, "ratio")
    row.pop("unit")
    calls = stub(client, response([row]))
    assert client.get_financial_metrics("PETR4", "2024-04-30")[0].return_on_equity == 0.12
    assert len(calls) == 1  # the row schema already carries its semantic unit


def test_ttm_does_not_use_quarterly_eps_or_fcf_per_share(client):
    calls = stub(client, response([
        metric("ltm:roe", 0.2, "ratio"),
        metric("eps", 5.0, "BRL/share"),
        metric("fcf_per_share", 3.0, "BRL/share"),
    ]))
    row = client.get_financial_metrics("PETR4", "2024-04-30", period="ttm")[0]
    assert row.earnings_per_share is None
    assert row.free_cash_flow_per_share is None
    assert calls[0]["params"]["period"] == "ltm"


def test_false_availability_flag_accepts_an_extensible_reason_and_maps_to_none(client):
    row = metric('peg_ratio', None, 'ratio', availability='insufficient_history')
    stub(client, response([row]))
    result = client.get_financial_metrics('PETR4', '2024-04-30', period='ttm')
    assert result[0].peg_ratio is None


def test_unknown_availability_reason_with_available_value_is_rejected(client):
    row = metric('peg_ratio', 1.2, 'ratio', availability='new_reason')
    stub(client, response([row]))
    with pytest.raises(CoverageError, match='unrecognized metric availability'):
        client.get_financial_metrics('PETR4', '2024-04-30', period='ttm')


def test_quarterly_per_share_keys_are_mapped_only_for_quarterly_requests(client):
    calls = stub(client, response([
        metric("eps", 2.5, "BRL/share", period="quarterly"),
        metric("fcf_per_share", 1.25, "BRL/share", period="quarterly"),
    ]))
    row = client.get_financial_metrics("PETR4", "2024-04-30", period="quarterly")[0]
    assert row.earnings_per_share == 2.5
    assert row.free_cash_flow_per_share == 1.25
    assert calls[0]["params"]["period"] == "quarterly"


def test_public_delivery_cutoff_selects_latest_eligible_version(client):
    rows = [
        metric("ltm:roe", 0.15, "ratio", delivery="2024-04-17", version=1),
        metric("ltm:roe", 0.25, "ratio", delivery="2024-05-15", version=2),
    ]
    stub(client, response(rows), response(rows))
    before_revision = client.get_financial_metrics("PETR4", "2024-05-01")[0]
    after_revision = client.get_financial_metrics("PETR4", "2024-05-31")[0]
    assert before_revision.return_on_equity == 0.15
    assert before_revision.filing_date == "2024-04-17"
    assert after_revision.return_on_equity == 0.25
    assert after_revision.filing_date == "2024-05-15"


def test_rows_without_delivery_date_are_excluded_not_assigned_a_filing_date(client):
    row = metric('ltm:roe', 0.15, 'ratio')
    row['source_delivery_date'] = None
    stub(client, response([row]))
    assert client.get_financial_metrics('PETR4', '2024-04-30') == []


@pytest.mark.parametrize("rows", [[metric("ltm:roe", 15, None)], [metric("ltm:roe", 15, "USD")],
    [metric("ltm:roe", float("inf"), "ratio")],
    [metric("ltm:roe", 0.15, "ratio"), metric("ltm:roe", 0.16, "ratio")], [{"value": 15}]])
def test_unknown_unit_nonfinite_conflicting_or_unknown_metric_schema_refused(client, rows):
    stub(client, response(rows), response([]))
    with pytest.raises(CoverageError):
        client.get_financial_metrics("PETR4", "2024-04-30")


def test_historical_market_cap_uses_dated_metrics_not_current_profile(client):
    calls = stub(client, response([metric("market_cap", 100, "BRL thousands")]))
    assert client.get_market_cap("PETR4", "2024-04-30") == 100_000
    assert calls[0]["url"].endswith("/financial-metrics")


def test_company_and_coverage_contract(client):
    calls = stub(client, response(dict(company_name="Synthetic Company", sector="Synthetic")),
                 response(dict(available=True)))
    facts = client.get_company_facts("PETR4")
    assert facts.name == "Synthetic Company" and facts.exchange == "B3"
    assert client.get_coverage("PETR4")["data"]["available"] is True
    assert calls[1]["url"].endswith("/companies/PETR4/coverage")


@pytest.mark.parametrize("method,args", [
    ("get_news", ("PETR4", "2024-01-31")),
])
def test_unavailable_or_unverified_capabilities_are_explicit_gaps(client, method, args):
    calls = stub(client)
    with pytest.raises(CoverageError):
        getattr(client, method)(*args)
    assert calls == []


def test_quarterly_metrics_do_not_request_or_relabel_ltm_flows(client):
    calls = stub(client, response([metric('roe', '0.07', 'ratio', period='quarterly'),
                                   metric('eps', '1.8', 'BRL/share', period='quarterly')]))
    rows = client.get_financial_metrics('PETR4', '2024-06-14', period='quarterly')
    assert calls[0]['params']['market_date_mode'] == 'statement_date'
    assert 'metric_keys' not in calls[0]['params']
    assert rows[0].period == 'quarterly'
    assert rows[0].return_on_equity == pytest.approx(0.07)
    assert rows[0].earnings_per_share == pytest.approx(1.8)


def test_quarterly_per_share_ytd_cannot_be_called_an_isolated_quarter(client):
    stub(client, response([metric('eps', '3.5', 'BRL/share', report='2024-06-30',
                                   period='quarterly', period_start='2024-01-01'),
                           metric('fcf_per_share', '4.0', 'BRL/share', report='2024-06-30',
                                   period='quarterly', period_start='2024-01-01'),
                           metric('market_cap', '1000', 'BRL', report='2024-06-30', period='quarterly')]))
    rows = client.get_financial_metrics('PETR4', '2024-09-01', period='quarterly')
    assert rows[0].market_cap == 1000
    assert rows[0].earnings_per_share is None and rows[0].free_cash_flow_per_share is None


def test_statement_client_combines_itr_and_dfp_without_announcements(client):
    from hedge_fund.data.test_statements import fact
    calls = stub(client, response([fact('revenue', '2024-03-31', '100', period_type='ytd')]),
                        response([fact('revenue', '2023-12-31', '300', period_kind='annual',
                                       period_type='annual', source_dataset='DFP', period_start='2023-01-01')]))
    earnings = client.get_earnings('petr4.sa')
    assert earnings.quarterly.revenue == 100000
    assert earnings.annual.revenue == 300000
    assert earnings.data_source == 'openmarkets' and earnings.point_in_time is False
    assert earnings.quarterly.estimated_earnings_per_share is None
    assert [call['params']['period'] for call in calls] == ['quarterly', 'annual']
    assert all('canonical_names' in call['params'] for call in calls)


def test_real_quotes_contract_keeps_every_price_on_adjusted_close_scale(client):
    row = dict(symbol='PETR4', trade_date='2024-06-14', open_price=35.4900016785,
        high_price=35.5, low_price=34.1500015259, close_price=34.6800003052,
        adjusted_close_price=26.3739109039, price='26.3739109039',
        tick_volume=53534900.0, currency='BRL', source='yfinance')
    stub(client, SyntheticResponse(payload={'data': [row], 'meta': {
        'price_basis': 'adjusted', 'ohlc_price_basis': 'raw', 'next_cursor': None}}))
    bar = client.get_prices('PETR4', '2024-06-14', '2024-06-14')[0]
    factor = row['adjusted_close_price'] / row['close_price']
    assert bar.close == pytest.approx(row['adjusted_close_price'])
    assert bar.open == pytest.approx(row['open_price'] * factor)
    assert bar.high == pytest.approx(row['high_price'] * factor)
    assert bar.low == pytest.approx(row['low_price'] * factor)
    assert bar.volume == 53534900
    assert bar.raw_close == row['close_price'] and bar.adjustment_factor == factor
    assert bar.adjusted_ohlc_derived and bar.price_basis_verified
    assert bar.data_source == 'openmarkets' and bar.underlying_source == 'yfinance'


def test_real_quotes_refuses_selected_price_inconsistent_with_adjustment(client):
    row = dict(trade_date='2024-06-14', open_price=35, high_price=36, low_price=34,
        close_price=35, adjusted_close_price=26, price='35', tick_volume=100, currency='BRL')
    stub(client, SyntheticResponse(payload={'data': [row], 'meta': {
        'price_basis': 'adjusted', 'ohlc_price_basis': 'raw'}}))
    with pytest.raises(CoverageError):
        client.get_prices('PETR4', '2024-06-14', '2024-06-14')


def test_real_insiders_filter_actual_day_and_preserve_aggregate_and_publication_gaps(client):
    row = dict(referencia_data='2024-06-01', dia=10, moeda='BRL', escopo='consolidada',
        origem='orgaos_tecnicos', entidade_nome='PETROBRAS', instrument_class='local_share',
        instrument_label='Ações PN', operacao='compra', quantidade='100', preco='37', volume_reais='3700')
    calls = stub(client, response([row, {**row, 'dia': 30},
        {**row, 'dia': None, 'operacao': 'sem_mov', 'quantidade': None, 'preco': None, 'volume_reais': None}]))
    trades = client.get_insider_trades('PETR4', '2024-06-14', '2024-06-05')
    assert len(trades) == 1 and trades[0].transaction_date == '2024-06-10'
    assert trades[0].name is None and trades[0].filing_date is None
    assert trades[0].is_aggregated and not trades[0].point_in_time
    assert trades[0].issuer == 'PETROBRAS' and trades[0].transaction_value == 3700
    assert trades[0].ticker_scope == 'reporting_company'
    assert calls[0]['params']['start_date'] == '2024-06-01'
    assert client.normalization_audit['/v1/governance/insiders']['non_movement_monthly_summaries'] == 1


def test_undated_actual_insider_movement_is_not_silently_discarded(client):
    stub(client, response([dict(referencia_data='2024-06-01', dia=None,
                               operacao='compra', quantidade='100', preco='37', volume_reais='3700')]))
    with pytest.raises(CoverageError, match='invalid dated movement'):
        client.get_insider_trades('PETR4', '2024-06-14', '2024-06-01')
