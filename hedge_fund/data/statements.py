"""Normalize OpenMarkets financial-statement facts into the legacy Earnings view.

The endpoint contains published accounting facts, not announcement records.
This module therefore maps statement periods and amounts only; it never
fabricates EPS, estimates, surprises, or publication dates.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any

from hedge_fund.data.models import Earnings, EarningsData, EarningsRecord


# Multiple canonical facts can describe a familiar application field. The
# order is intentional: an explicitly named aggregate wins over a component
# fallback, and net_income wins over its common-share alias.
_FLOW_FIELDS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue",),
    "net_income": ("net_income", "net_income_common"),
    "gross_profit": ("gross_profit",),
    "operating_income": ("operating_income", "ebit"),
    "net_cash_flow_from_operations": ("operating_cash_flow",),
    "capital_expenditure": ("capex",),
    "net_cash_flow_from_investing": ("investing_cash_flow",),
    "net_cash_flow_from_financing": ("financing_cash_flow",),
    "free_cash_flow": ("free_cash_flow",),
}
_STOCK_FIELDS: dict[str, tuple[str, ...]] = {
    "cash_and_equivalents": ("cash_and_equivalents",),
    "total_assets": ("total_assets",),
    "shareholders_equity": ("shareholders_equity", "equity"),
    "total_debt": ("total_debt",),
    "total_liabilities": ("total_liabilities",),
}
STATEMENT_CANONICALS = frozenset({
    canonical
    for alternatives in (*_FLOW_FIELDS.values(), *_STOCK_FIELDS.values())
    for canonical in alternatives
} | {
    "short_term_debt",
    "long_term_debt",
    "current_liabilities",
    "non_current_liabilities",
})
@dataclass(frozen=True)
class _Fact:
    canonical: str
    period_end: date
    period_start: date | None
    period_type: str | None
    period_kind: str | None
    source_dataset: str | None
    consolidation: str | None
    version: int
    amount: Decimal


def _parse_date(value: Any, *, field: str, canonical: str) -> date:
    try:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        pass
    raise ValueError(f"{canonical}: invalid {field} {value!r}")


def _parse_amount(row: Mapping[str, Any], canonical: str) -> Decimal | None:
    raw = row.get("value")
    if raw is None:
        return None
    try:
        amount = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{canonical}: invalid monetary value") from None
    if not amount.is_finite():
        raise ValueError(f"{canonical}: monetary value must be finite")

    currency = str(row.get("currency") or "").strip().upper()
    scale = str(row.get("currency_scale") or "").strip().upper()
    if currency not in {"REAL", "BRL"}:
        raise ValueError(f"{canonical}: unsupported currency {currency or None!r}")
    if scale != "MIL":
        raise ValueError(f"{canonical}: unsupported currency_scale {scale or None!r}")
    # OpenMarkets MIL facts are in BRL thousands; Earnings stores absolute BRL.
    return amount * Decimal(1_000)


def _parse_rows(rows: Sequence[Mapping[str, Any]] | None, expected_kind: str) -> list[_Fact]:
    if rows is None:
        return []
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise ValueError(f"{expected_kind}_rows must be a sequence of mappings")

    facts: list[_Fact] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"{expected_kind}_rows contains a non-mapping row")
        canonical = str(row.get("canonical_name") or "").strip().casefold()
        if canonical not in STATEMENT_CANONICALS:
            continue
        amount = _parse_amount(row, canonical)
        if amount is None:
            continue

        period_kind_raw = row.get("period_kind")
        period_kind = str(period_kind_raw).strip().casefold() if period_kind_raw else None
        if period_kind is not None and period_kind != expected_kind:
            raise ValueError(
                f"{canonical}: expected period_kind={expected_kind!r}, got {period_kind!r}"
            )
        end = _parse_date(row.get("period_end"), field="period_end", canonical=canonical)
        start_raw = row.get("period_start")
        start = _parse_date(start_raw, field="period_start", canonical=canonical) if start_raw else None

        period_type_raw = row.get("period_type")
        period_type = (
            str(period_type_raw).strip().casefold().replace("-", "_")
            if period_type_raw
            else None
        )
        version_raw = row.get("version", 0)
        try:
            version = int(version_raw) if version_raw is not None else 0
        except (TypeError, ValueError):
            raise ValueError(f"{canonical}: invalid version {version_raw!r}") from None
        if version < 0:
            raise ValueError(f"{canonical}: version must be non-negative")

        source = str(row.get("source_dataset") or "").strip().upper() or None
        consolidation_raw = str(row.get("consolidation") or "").strip().casefold()
        consolidation = consolidation_raw or None
        facts.append(
            _Fact(
                canonical=canonical,
                period_end=end,
                period_start=start,
                period_type=period_type,
                period_kind=period_kind,
                source_dataset=source,
                consolidation=consolidation,
                version=version,
                amount=amount,
            )
        )
    return facts


def _cohort_by_date(facts: list[_Fact], preferred_dataset: str) -> list[_Fact]:
    """Choose one consolidation and dataset per period, avoiding mixed facts."""
    grouped: dict[date, list[_Fact]] = defaultdict(list)
    for fact in facts:
        grouped[fact.period_end].append(fact)

    chosen: list[_Fact] = []
    for end in sorted(grouped):
        rows = grouped[end]
        consolidations = {row.consolidation for row in rows}
        if "consolidado" in consolidations:
            consolidation = "consolidado"
        elif len(consolidations) == 1:
            consolidation = next(iter(consolidations))
        else:
            raise ValueError(f"{end}: conflicting consolidation scopes")
        rows = [row for row in rows if row.consolidation == consolidation]

        datasets = {row.source_dataset for row in rows}
        if preferred_dataset in datasets:
            dataset = preferred_dataset
        elif len(datasets) == 1:
            dataset = next(iter(datasets))
        else:
            raise ValueError(f"{end}: conflicting source datasets")
        chosen.extend(row for row in rows if row.source_dataset == dataset)

    return _deduplicate_facts(chosen)


def _deduplicate_facts(facts: list[_Fact]) -> list[_Fact]:
    grouped: dict[tuple[date, str, str | None], list[_Fact]] = defaultdict(list)
    for fact in facts:
        grouped[(fact.period_end, fact.canonical, fact.period_type)].append(fact)

    result: list[_Fact] = []
    for key, rows in grouped.items():
        newest_version = max(row.version for row in rows)
        newest = [row for row in rows if row.version == newest_version]
        amounts = {row.amount for row in newest}
        starts = {row.period_start for row in newest}
        if len(amounts) != 1 or len(starts) != 1:
            end, canonical, _ = key
            raise ValueError(f"{canonical} at {end}: ambiguous duplicate facts at latest version")
        # Equal duplicate facts are harmless; retain one row in a stable order.
        result.append(sorted(newest, key=lambda row: (row.source_dataset or "", row.consolidation or ""))[0])
    return result


def _index(facts: list[_Fact]) -> dict[tuple[date, str, str | None], _Fact]:
    return {(row.period_end, row.canonical, row.period_type): row for row in facts}


def _get(
    facts: dict[tuple[date, str, str | None], _Fact],
    end: date,
    canonicals: tuple[str, ...],
    period_types: tuple[str | None, ...],
) -> _Fact | None:
    for canonical in canonicals:
        for period_type in period_types:
            row = facts.get((end, canonical, period_type))
            if row is not None:
                return row
    return None


def _quarter_number(end: date) -> int:
    if end.month not in {3, 6, 9, 12} or end.day != calendar.monthrange(end.year, end.month)[1]:
        raise ValueError(f"{end}: cannot identify a calendar fiscal quarter")
    return end.month // 3


def _previous_quarter_end(end: date) -> date:
    quarter = _quarter_number(end)
    if quarter == 1:
        raise ValueError(f"{end}: Q1 has no same-year YTD predecessor")
    prior_month = end.month - 3
    return date(end.year, prior_month, calendar.monthrange(end.year, prior_month)[1])


def _valid_ytd_start(row: _Fact) -> bool:
    """CVM calendar-year YTD facts must share a Jan 1 start."""
    return row.period_start == date(row.period_end.year, 1, 1)


def _row_matches(row: _Fact | None, other: _Fact | None) -> bool:
    return row is not None and other is not None and (
        row.source_dataset == other.source_dataset
        and row.consolidation == other.consolidation
    )


def _flow_for_quarter(
    quarterly: dict[tuple[date, str, str | None], _Fact],
    annual: dict[tuple[date, str, str | None], _Fact],
    end: date,
    canonicals: tuple[str, ...],
    annual_end: date | None,
) -> Decimal | None:
    quarter = _quarter_number(end)

    # An explicit isolated Q4 fact is the strongest evidence, wherever it is
    # present. Otherwise a full-year amount less Q3 YTD is valid only for the
    # matching calendar year and consolidation scope.
    if quarter == 4 and annual_end == end:
        direct = _get(quarterly, end, canonicals, ("quarterly_standalone",))
        if direct is None:
            direct = _get(annual, end, canonicals, ("quarterly_standalone",))
        if direct is not None:
            return direct.amount

        annual_fact = _get(annual, end, canonicals, ("annual", "annual_standalone", "full_year", "fiscal_year", "fy", "ytd", None))
        q3_end = date(end.year, 9, 30)
        q3_ytd = _get(quarterly, q3_end, canonicals, ("ytd",))
        if annual_fact is None or q3_ytd is None:
            return None
        if annual_fact.period_type == "ytd" and not _valid_ytd_start(annual_fact):
            return None
        if not _valid_ytd_start(q3_ytd):
            return None
        if annual_fact.period_start is not None and q3_ytd.period_start is not None:
            if annual_fact.period_start != q3_ytd.period_start:
                return None
        if annual_fact.consolidation != q3_ytd.consolidation:
            return None
        return annual_fact.amount - q3_ytd.amount

    direct = _get(quarterly, end, canonicals, ("quarterly_standalone",))
    if direct is not None:
        return direct.amount

    ytd = _get(quarterly, end, canonicals, ("ytd",))
    if ytd is None:
        return None
    if quarter == 1:
        return ytd.amount if ytd.period_start is None or _valid_ytd_start(ytd) else None
    if quarter == 4:
        return None

    previous = _get(quarterly, _previous_quarter_end(end), canonicals, ("ytd",))
    if previous is None or not _row_matches(ytd, previous):
        return None
    if not _valid_ytd_start(ytd) or not _valid_ytd_start(previous):
        return None
    return ytd.amount - previous.amount


def _annual_flow(
    annual: dict[tuple[date, str, str | None], _Fact],
    end: date,
    canonicals: tuple[str, ...],
) -> Decimal | None:
    row = _get(annual, end, canonicals, ("annual", "annual_standalone", "full_year", "fiscal_year", "fy", "ytd", None))
    if row is None:
        return None
    if row.period_type == "ytd" and not _valid_ytd_start(row):
        return None
    # For annual_rows, period_kind=annual is itself the annual contract. A YTD
    # row is accepted only when it explicitly spans Jan 1 through year-end.
    return row.amount


def _stock_value(
    facts: dict[tuple[date, str, str | None], _Fact],
    end: date,
    canonicals: tuple[str, ...],
) -> Decimal | None:
    # A balance sheet is a point-in-time snapshot. Never subtract snapshots.
    row = _get(facts, end, canonicals, ("ytd", "quarterly_standalone", "annual", "annual_standalone", "full_year", "fiscal_year", "fy", None))
    return row.amount if row is not None else None


def _component_sum(
    facts: dict[tuple[date, str, str | None], _Fact],
    end: date,
    first: str,
    second: str,
) -> Decimal | None:
    left = _stock_value(facts, end, (first,))
    right = _stock_value(facts, end, (second,))
    return left + right if left is not None and right is not None else None


def _to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    converted = float(value)
    if not isfinite(converted):
        raise ValueError("normalized monetary value is outside the finite float range")
    return converted


def _make_quarterly(
    quarterly_rows: list[_Fact], annual_rows: list[_Fact], end: date, annual_end: date | None
) -> EarningsData | None:
    _quarter_number(end)
    quarterly = _index(quarterly_rows)
    annual = _index(annual_rows)
    values: dict[str, float | None] = {}

    for field, canonicals in _FLOW_FIELDS.items():
        amount = _flow_for_quarter(quarterly, annual, end, canonicals, annual_end)
        values[field] = _to_float(amount)

    stock_rows = annual_rows if annual_end == end else quarterly_rows
    stocks = _index(stock_rows)
    for field, canonicals in _STOCK_FIELDS.items():
        amount = _stock_value(stocks, end, canonicals)
        if field == "total_debt" and amount is None:
            amount = _component_sum(stocks, end, "short_term_debt", "long_term_debt")
        elif field == "total_liabilities" and amount is None:
            amount = _component_sum(stocks, end, "current_liabilities", "non_current_liabilities")
        values[field] = _to_float(amount)

    op_cash_flow = values.get("net_cash_flow_from_operations")
    capex = values.get("capital_expenditure")
    if values.get("free_cash_flow") is None and op_cash_flow is not None and capex is not None:
        # OpenMarkets capex follows the cash-flow sign convention (outflows are
        # negative), as in the captured CVM ITR sample.
        values["free_cash_flow"] = op_cash_flow + capex

    data = EarningsData(**values)
    return data if data.model_dump(exclude_none=True) else None


def _make_annual(rows: list[_Fact], end: date) -> EarningsData | None:
    facts = _index(rows)
    values: dict[str, float | None] = {}
    for field, canonicals in _FLOW_FIELDS.items():
        values[field] = _to_float(_annual_flow(facts, end, canonicals))
    for field, canonicals in _STOCK_FIELDS.items():
        amount = _stock_value(facts, end, canonicals)
        if field == "total_debt" and amount is None:
            amount = _component_sum(facts, end, "short_term_debt", "long_term_debt")
        elif field == "total_liabilities" and amount is None:
            amount = _component_sum(facts, end, "current_liabilities", "non_current_liabilities")
        values[field] = _to_float(amount)

    op_cash_flow = values.get("net_cash_flow_from_operations")
    capex = values.get("capital_expenditure")
    if values.get("free_cash_flow") is None and op_cash_flow is not None and capex is not None:
        values["free_cash_flow"] = op_cash_flow + capex

    data = EarningsData(**values)
    return data if data.model_dump(exclude_none=True) else None


def _latest_quarter_end(quarterly: list[_Fact], annual_end: date | None) -> date | None:
    quarter_ends = [row.period_end for row in quarterly]
    latest_quarter = max(quarter_ends) if quarter_ends else None
    if annual_end is not None and annual_end.month == 12 and annual_end.day == 31:
        if latest_quarter is None or annual_end >= latest_quarter:
            return annual_end
    return latest_quarter


def _fiscal_period(report_period: date, *, quarterly_available: bool) -> str:
    if quarterly_available:
        return f"Q{_quarter_number(report_period)} {report_period.year}"
    if report_period.month == 12 and report_period.day == 31:
        return f"FY {report_period.year}"
    return f"FY ended {report_period.isoformat()}"


def normalize_statements(
    ticker: str,
    quarterly_rows: Sequence[Mapping[str, Any]] | None,
    annual_rows: Sequence[Mapping[str, Any]] | None,
) -> Earnings | None:
    """Return the latest normalized OpenMarkets Earnings view.

    ``quarterly_rows`` and ``annual_rows`` are the endpoint's ``data`` arrays.
    Monetary facts with ``currency_scale=MIL`` become absolute BRL values.
    Quarterly YTD flows are differenced only against the immediately previous
    same-source, same-consolidation YTD period. Annual minus Q3 YTD is used for
    Q4 only when both are present and the calendar-year periods agree.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker must be non-empty")
    quarterly = _cohort_by_date(_parse_rows(quarterly_rows, "quarterly"), "ITR")
    annual = _cohort_by_date(_parse_rows(annual_rows, "annual"), "DFP")
    quarterly_end = max((row.period_end for row in quarterly), default=None)
    annual_end = max((row.period_end for row in annual), default=None)
    quarter_end = _latest_quarter_end(quarterly, annual_end)

    quarterly_data = (
        _make_quarterly(quarterly, annual, quarter_end, annual_end)
        if quarter_end is not None
        else None
    )
    annual_data = _make_annual(annual, annual_end) if annual_end is not None else None
    available_periods = [period for period in (quarter_end, annual_end) if period is not None]
    if not available_periods:
        return None
    report_period = max(available_periods)
    quarterly_is_latest = quarterly_data is not None and quarter_end == report_period
    fiscal_period = _fiscal_period(report_period, quarterly_available=quarterly_is_latest)
    if quarterly_data is None and annual_data is None:
        return None
    return Earnings(
        ticker=ticker,
        report_period=report_period.isoformat(),
        fiscal_period=fiscal_period,
        currency="BRL",
        data_source="openmarkets",
        point_in_time=False,
        quarterly=quarterly_data,
        annual=annual_data,
    )


def normalize_statement_history(
    ticker: str,
    quarterly_rows: Sequence[Mapping[str, Any]] | None,
    annual_rows: Sequence[Mapping[str, Any]] | None,
    limit: int = 12,
) -> list[EarningsRecord]:
    """Normalize period history without claiming announcement timestamps.

    Quarterly rows produce ITR records; annual rows produce DFP records. A
    DFP Q4 record includes a derived quarterly view only when annual and Q3 YTD
    facts are date-compatible. ``filing_date`` and related publication fields
    remain ``None`` because statement period ends are not publication dates.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker must be non-empty")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")

    quarterly = _cohort_by_date(_parse_rows(quarterly_rows, "quarterly"), "ITR")
    annual = _cohort_by_date(_parse_rows(annual_rows, "annual"), "DFP")
    quarterly_index = _index(quarterly)
    annual_dates = {row.period_end for row in annual}
    records: list[EarningsRecord] = []

    def has_standalone_q4(end: date) -> bool:
        return any(
            _get(quarterly_index, end, canonicals, ("quarterly_standalone",)) is not None
            for canonicals in _FLOW_FIELDS.values()
        )

    for end in sorted({row.period_end for row in quarterly}):
        quarter = _quarter_number(end)
        annual_end = date(end.year, 12, 31) if date(end.year, 12, 31) in annual_dates else None
        # If the Q4 view has no standalone ITR flow, attach its supported
        # residual to the DFP annual record below rather than mislabeling it ITR.
        if quarter == 4 and annual_end == end and not has_standalone_q4(end):
            continue
        data = _make_quarterly(quarterly, [], end, None)
        if data is None:
            continue
        records.append(
            EarningsRecord(
                ticker=ticker,
                report_period=end.isoformat(),
                source_type="ITR",
                data_source="openmarkets",
                point_in_time=False,
                filing_date=None,
                filing_datetime=None,
                filing_window=None,
                fiscal_period=f"Q{quarter} {end.year}",
                currency="BRL",
                quarterly=data,
            )
        )

    for end in sorted(annual_dates):
        annual_data = _make_annual(annual, end)
        q4_data = None
        if end.month == 12 and end.day == 31 and not has_standalone_q4(end):
            q4_data = _make_quarterly(quarterly, annual, end, end)
        if annual_data is None and q4_data is None:
            continue
        fiscal_period = (
            f"FY {end.year}"
            if end.month == 12 and end.day == 31
            else f"FY ended {end.isoformat()}"
        )
        records.append(
            EarningsRecord(
                ticker=ticker,
                report_period=end.isoformat(),
                source_type="DFP",
                data_source="openmarkets",
                point_in_time=False,
                filing_date=None,
                filing_datetime=None,
                filing_window=None,
                fiscal_period=fiscal_period,
                currency="BRL",
                quarterly=q4_data,
                annual=annual_data,
            )
        )

    records.sort(key=lambda record: (record.report_period, record.source_type), reverse=True)
    return records[:limit]
