"""Unit tests for financial-statement to Earnings normalization."""
import pytest

from hedge_fund.data.models import Earnings, EarningsRecord
from hedge_fund.data.statements import normalize_statement_history, normalize_statements


def fact(
    canonical,
    period_end,
    value,
    *,
    period_kind="quarterly",
    period_type="quarterly_standalone",
    period_start="2024-01-01",
    source_dataset="ITR",
    consolidation="consolidado",
    version=1,
    currency="REAL",
    currency_scale="MIL",
    **extra,
):
    return {
        "canonical_name": canonical,
        "period_kind": period_kind,
        "period_type": period_type,
        "period_start": period_start,
        "period_end": period_end,
        "reference_date": period_end,
        "source_dataset": source_dataset,
        "source_document_id": "fixture-document",
        "consolidation": consolidation,
        "version": version,
        "currency": currency,
        "currency_scale": currency_scale,
        "value": value,
        **extra,
    }


def test_minimized_petr4_page_maps_br_mil_and_leaves_estimates_and_shares_null():
    # Minimized facts from the captured PETR4 ITR page, with the same values
    # and semantics but no opaque fact/document identifiers.
    rows = [
        fact("revenue", "2024-03-31", "117721000", period_start="2024-01-01"),
        fact("gross_profit", "2024-03-31", "60701000", period_start="2024-01-01"),
        fact("ebit", "2024-03-31", "44027000", period_start="2024-01-01"),
        fact("net_income", "2024-03-31", "23700000", period_start="2024-01-01"),
        fact("net_income_common", "2024-03-31", "23810000", period_start="2024-01-01"),
        fact("operating_cash_flow", "2024-03-31", "46481000", period_start="2024-01-01"),
        fact("capex", "2024-03-31", "-14049000", period_start="2024-01-01"),
        fact("investing_cash_flow", "2024-03-31", "-16440000", period_start="2024-01-01"),
        fact("cash_and_equivalents", "2024-03-31", "57689000", period_type="ytd", period_start=None),
        fact("total_assets", "2024-03-31", "1067292000", period_type="ytd", period_start=None),
        fact("equity", "2024-03-31", "409922000", period_type="ytd", period_start=None),
        # These names are intentionally not part of the canonical mapping.
        fact("weighted_average_shares", "2024-03-31", "1000000", period_start="2024-01-01"),
    ]

    result = normalize_statements("petr4", rows, [])

    assert result is not None
    assert result.ticker == "PETR4"
    assert result.report_period == "2024-03-31"
    assert result.fiscal_period == "Q1 2024"
    assert result.currency == "BRL"
    assert result.data_source == "openmarkets" and result.point_in_time is False
    assert result.quarterly is not None
    assert result.quarterly.revenue == 117_721_000_000
    assert result.quarterly.net_income == 23_700_000_000  # preferred over the alias
    assert result.quarterly.gross_profit == 60_701_000_000
    assert result.quarterly.operating_income == 44_027_000_000
    assert result.quarterly.cash_and_equivalents == 57_689_000_000
    assert result.quarterly.total_assets == 1_067_292_000_000
    assert result.quarterly.shareholders_equity == 409_922_000_000
    assert result.quarterly.free_cash_flow == 32_432_000_000
    assert result.quarterly.earnings_per_share is None
    assert result.quarterly.estimated_earnings_per_share is None
    assert result.quarterly.eps_surprise is None
    assert result.quarterly.weighted_average_shares is None


def test_q2_ytd_flows_are_differenced_but_balance_sheet_values_are_not():
    rows = [
        fact("revenue", "2024-03-31", "100", period_type="ytd", period_start="2024-01-01"),
        fact("revenue", "2024-06-30", "220", period_type="ytd", period_start="2024-01-01"),
        fact("operating_cash_flow", "2024-03-31", "80", period_type="ytd", period_start="2024-01-01"),
        fact("operating_cash_flow", "2024-06-30", "190", period_type="ytd", period_start="2024-01-01"),
        fact("capex", "2024-03-31", "-20", period_type="ytd", period_start="2024-01-01"),
        fact("capex", "2024-06-30", "-45", period_type="ytd", period_start="2024-01-01"),
        fact("cash_and_equivalents", "2024-03-31", "50", period_type="ytd", period_start=None),
        fact("cash_and_equivalents", "2024-06-30", "70", period_type="ytd", period_start=None),
    ]

    result = normalize_statements("PETR4", rows, None)

    assert result is not None and result.quarterly is not None
    assert result.report_period == "2024-06-30"
    assert result.fiscal_period == "Q2 2024"
    assert result.quarterly.revenue == 120_000
    assert result.quarterly.net_cash_flow_from_operations == 110_000
    assert result.quarterly.capital_expenditure == -25_000
    assert result.quarterly.free_cash_flow == 85_000
    assert result.quarterly.cash_and_equivalents == 70_000


def test_explicit_standalone_quarter_wins_over_same_period_ytd():
    rows = [
        fact("revenue", "2024-06-30", "220", period_type="ytd", period_start="2024-01-01"),
        fact("revenue", "2024-06-30", "120", period_type="quarterly_standalone", period_start="2024-04-01"),
    ]

    result = normalize_statements("PETR4", rows, None)

    assert result is not None and result.quarterly is not None
    assert result.quarterly.revenue == 120_000


def test_q4_is_full_year_less_compatible_q3_ytd_and_annual_remains_full_year():
    quarterly = [
        fact("revenue", "2023-09-30", "350", period_start="2023-01-01", period_type="ytd"),
        fact("operating_cash_flow", "2023-09-30", "220", period_start="2023-01-01", period_type="ytd"),
        fact("capex", "2023-09-30", "-70", period_start="2023-01-01", period_type="ytd"),
    ]
    annual = [
        fact("revenue", "2023-12-31", "500", period_kind="annual", period_type="annual",
             period_start="2023-01-01", source_dataset="DFP"),
        fact("operating_cash_flow", "2023-12-31", "300", period_kind="annual", period_type="annual",
             period_start="2023-01-01", source_dataset="DFP"),
        fact("capex", "2023-12-31", "-90", period_kind="annual", period_type="annual",
             period_start="2023-01-01", source_dataset="DFP"),
        fact("cash_and_equivalents", "2023-12-31", "65", period_kind="annual", period_type="annual",
             period_start=None, source_dataset="DFP"),
    ]

    result = normalize_statements("PETR4", quarterly, annual)

    assert result is not None and result.quarterly is not None and result.annual is not None
    assert result.report_period == "2023-12-31"
    assert result.fiscal_period == "Q4 2023"
    assert result.quarterly.revenue == 150_000
    assert result.quarterly.net_cash_flow_from_operations == 80_000
    assert result.quarterly.capital_expenditure == -20_000
    assert result.quarterly.free_cash_flow == 60_000
    assert result.quarterly.cash_and_equivalents == 65_000
    assert result.annual.revenue == 500_000
    assert result.annual.net_cash_flow_from_operations == 300_000
    assert result.annual.cash_and_equivalents == 65_000


def test_q4_flows_remain_missing_without_q3_ytd_and_stocks_are_still_available():
    annual = [
        fact("revenue", "2023-12-31", "500", period_kind="annual", period_type="annual",
             period_start="2023-01-01", source_dataset="DFP"),
        fact("cash_and_equivalents", "2023-12-31", "65", period_kind="annual", period_type="annual",
             period_start=None, source_dataset="DFP"),
    ]

    result = normalize_statements("PETR4", [], annual)

    assert result is not None and result.quarterly is not None
    assert result.quarterly.revenue is None
    assert result.quarterly.cash_and_equivalents == 65_000
    assert result.annual is not None and result.annual.revenue == 500_000


def test_latest_version_wins_and_conflicting_latest_duplicates_are_rejected():
    rows = [
        fact("revenue", "2024-03-31", "100", version=1),
        fact("revenue", "2024-03-31", "110", version=2),
        fact("revenue", "2024-03-31", "110", version=2, source_document_id="duplicate"),
    ]
    result = normalize_statements("PETR4", rows, [])
    assert result is not None and result.quarterly is not None
    assert result.quarterly.revenue == 110_000

    conflicting = [*rows[:-1], fact("revenue", "2024-03-31", "111", version=2)]
    with pytest.raises(ValueError, match="ambiguous duplicate facts"):
        normalize_statements("PETR4", conflicting, [])


@pytest.mark.parametrize(
    "overrides, message",
    [({"currency": "USD"}, "unsupported currency"), ({"currency_scale": "MILLION"}, "unsupported currency_scale")],
)
def test_unknown_currency_or_scale_is_rejected(overrides, message):
    rows = [fact("revenue", "2024-03-31", "100", **overrides)]
    with pytest.raises(ValueError, match=message):
        normalize_statements("PETR4", rows, [])


def test_unmatched_ytd_source_does_not_mix_datasets_or_fill_a_missing_quarter_with_zero():
    rows = [
        fact("revenue", "2024-03-31", "100", period_type="ytd", period_start="2024-01-01",
             source_dataset="DFP"),
        fact("revenue", "2024-06-30", "240", period_type="ytd", period_start="2024-01-01",
             source_dataset="ITR"),
        fact("cash_and_equivalents", "2024-06-30", "70", period_type="ytd", period_start=None,
             source_dataset="ITR"),
    ]

    result = normalize_statements("PETR4", rows, None)

    assert result is not None and result.quarterly is not None
    assert result.quarterly.revenue is None


def test_empty_statement_pages_return_none():
    assert normalize_statements("PETR4", [], []) is None


def test_history_labels_itr_and_dfps_and_never_invents_publication_dates():
    quarterly = [
        fact("revenue", "2023-03-31", "100", period_start="2023-01-01", period_type="ytd"),
        fact("revenue", "2023-06-30", "220", period_start="2023-01-01", period_type="ytd"),
        fact("revenue", "2023-09-30", "350", period_start="2023-01-01", period_type="ytd"),
    ]
    annual = [
        fact("revenue", "2023-12-31", "500", period_kind="annual", period_type="annual",
             period_start="2023-01-01", source_dataset="DFP"),
    ]

    history = normalize_statement_history("PETR4", quarterly, annual, limit=2)

    assert [(row.report_period, row.source_type) for row in history] == [
        ("2023-12-31", "DFP"),
        ("2023-09-30", "ITR"),
    ]
    assert history[0].annual is not None and history[0].annual.revenue == 500_000
    assert history[0].quarterly is not None and history[0].quarterly.revenue == 150_000
    assert history[0].filing_date is None and history[0].filing_datetime is None
    assert history[0].data_source == "openmarkets" and history[0].point_in_time is False
    assert history[1].fiscal_period == "Q3 2023"
    assert history[1].filing_date is None


def test_history_limit_must_be_positive():
    with pytest.raises(ValueError, match="positive integer"):
        normalize_statement_history("PETR4", [], [], limit=0)


def test_earnings_model_provenance_defaults_remain_provider_neutral():
    latest = Earnings(ticker="PETR4", report_period="2024-03-31")
    record = EarningsRecord(ticker="PETR4", report_period="2024-03-31", source_type="ITR")

    assert latest.data_source == record.data_source == "unspecified"
    assert latest.point_in_time is True and record.point_in_time is True
