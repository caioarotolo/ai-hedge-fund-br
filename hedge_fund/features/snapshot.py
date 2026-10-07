"""Fundamentals snapshot — the shared input for LLM analysts.

A `FundamentalsSnapshot` is everything an investor agent is allowed to know
about a company at a given cutoff plus aggregates computed here in Python.
Providers with verified public availability support point-in-time snapshots;
providers without original disclosure vintages support exploratory snapshots
only, with that limitation carried in metadata and the rendered prompt.

The snapshot is pure data: build it once, hash it, feed it to any persona.
`content_hash` is the cache key for LLM calls — an agent only re-reasons
when a new filing changes its snapshot. Both the hash and `render()` exclude
`as_of`: two dates between filings see identical data, and identical data
must produce an identical prompt (a cache hit), not two paid LLM calls.
"""

from __future__ import annotations

import hashlib
from datetime import date

from pydantic import BaseModel

from hedge_fund.data.protocol import DataClient
from hedge_fund.features.breakpoints import MEBreakpoints

# An agent can't say anything defensible about a company with less history
# than this (one year of ttm rows).
MIN_PERIODS = 4


class InsufficientData(ValueError):
    """Not enough point-in-time history to build a snapshot."""


class PeriodFundamentals(BaseModel):
    """One reporting period's key metrics, compacted for prompting."""

    report_period: str
    filing_date: str | None = None
    market_cap: float | None = None
    price_to_earnings_ratio: float | None = None
    return_on_equity: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    revenue_growth: float | None = None
    earnings_per_share: float | None = None
    book_value_per_share: float | None = None
    free_cash_flow_per_share: float | None = None


class FundamentalsSnapshot(BaseModel):
    """What an analyst may know about *ticker* as of *as_of*. Newest first."""

    ticker: str
    as_of: str
    # False when the provider cannot establish original public availability
    # and vintage. Such snapshots support exploratory replay, not PIT claims.
    point_in_time: bool = True
    source: str | None = None
    currency: str | None = None
    sector: str | None = None
    industry: str | None = None
    periods: list[PeriodFundamentals]

    # Derived aggregates (computed in build_snapshot, not by the LLM)
    roe_avg: float | None = None
    net_margin_avg: float | None = None
    gross_margin_trend: float | None = None  # latest minus oldest
    bvps_cagr: float | None = None
    debt_to_equity_latest: float | None = None
    market_cap_latest: float | None = None
    # Latest EPS versus the same calendar quarter one year earlier. None
    # when that period is absent or its EPS is missing/non-positive.
    eps_growth_yoy: float | None = None
    # Size as a market-cap percentile of US stocks (steps of 5) as of the t-0
    # filing. Set only when build_snapshot is given a breakpoints table; None
    # means the blind prompt shows no size at all.
    size_percentile: int | None = None

    @property
    def content_hash(self) -> str:
        """Stable hash of the fundamentals content — the LLM cache key.

        Excludes `as_of` so two dates between filings share one hash: an
        unchanged snapshot must be free, not a fresh LLM call per date.
        """
        canonical = self.model_dump_json(exclude={"as_of"})
        return hashlib.sha256(canonical.encode()).hexdigest()[:24]

    def render(self, blind: bool = False) -> str:
        """Compact text block for the LLM prompt.

        Deliberately date-free (no `as_of`): the prompt cache keys on exact
        prompt text, so the same fundamentals must render identically on any
        date. It also keeps the LLM from anchoring on a calendar date it
        could associate with post-date world events.

        `blind=True` goes one step further, and is what backtests use: the
        ticker, industry, calendar dates, absolute size, and per-share dollar
        values are withheld (the sector stays). Periods are labelled t-0
        (latest), t-1, ..., per-share series are indexed to the oldest
        period shown (= 100), and size appears only as a market-cap
        percentile when a breakpoints table was supplied. Without that, a
        model that remembers how a named company did after a given quarter
        can recall the outcome it is being scored on. Blind mode reduces
        that recall rather than removing it (distinctive ratio profiles can
        still give a large company away), and the personas lose
        company-specific knowledge. Live runs render unblinded.
        """
        if blind:
            return self._render_blind()
        lines = [
            f"Company: {self.ticker}"
            + (f"  |  Sector: {self.sector}" if self.sector else "")
            + (f"  |  Industry: {self.industry}" if self.industry else ""),
            ("All figures below were publicly filed by their filing dates. "
             "Treat the most recent filing shown as the present."
             if self.point_in_time else self._historical_warning()),
            "",
            "Summary:",
            f"  Market cap ({'latest filed' if self.point_in_time else 'latest period'}): {_fmt(self.market_cap_latest)}",
            f"  ROE avg: {_fmt(self.roe_avg)}  |  Net margin avg: {_fmt(self.net_margin_avg)}",
            f"  Gross margin trend (latest-oldest): {_fmt(self.gross_margin_trend)}",
            f"  Book value/share CAGR: {_fmt(self.bvps_cagr)}",
            f"  Debt/equity (latest): {_fmt(self.debt_to_equity_latest)}",
            "",
            "History (trailing-twelve-month periods, newest first):",
            "period | filed | mktcap | P/E | ROE | gross_m | op_m | net_m | D/E "
            "| curr | rev_gr | EPS | BVPS | FCF/sh",
        ]
        for p in self.periods:
            lines.append(
                f"{p.report_period} | {p.filing_date or '?'} | {_fmt(p.market_cap)} "
                f"| {_fmt(p.price_to_earnings_ratio)} | {_fmt(p.return_on_equity)} "
                f"| {_fmt(p.gross_margin)} | {_fmt(p.operating_margin)} "
                f"| {_fmt(p.net_margin)} | {_fmt(p.debt_to_equity)} "
                f"| {_fmt(p.current_ratio)} | {_fmt(p.revenue_growth)} "
                f"| {_fmt(p.earnings_per_share)} | {_fmt(p.book_value_per_share)} "
                f"| {_fmt(p.free_cash_flow_per_share)}"
            )
        if self.source or self.currency:
            lines.insert(1, self._provenance())
        return "\n".join(lines)

    def _provenance(self) -> str:
        return f"Data source: {self.source or 'unspecified'} | Monetary units: {self.currency or 'unspecified'}"

    def _historical_warning(self) -> str:
        return ("EXPLORATORY DATA: original public availability and historical "
                "vintages are not verified. These figures may contain later "
                "restatements. Period end dates are not publication dates; "
                "do not interpret this replay as a reliable historical backtest.")

    def _render_blind(self) -> str:
        """Backtest prompt: ratios and indexed trends, no identifying dollars."""
        oldest = self.periods[-1]
        lines = [
            f"Company: (withheld)"
            + (f"  |  Sector: {self.sector}" if self.sector else ""),
            "Periods are labelled relative to the latest reporting period (t-0). "
            "Calendar dates, absolute size and per-share monetary values are withheld; "
            "per-share figures are indexed to the oldest period shown (= 100). "
            "Treat t-0 as the present.",
            "",
            "Summary:",
        ]
        if not self.point_in_time:
            lines.insert(1, self._historical_warning())
        if self.source or self.currency:
            lines.insert(1, self._provenance())
        if self.size_percentile is not None:
            lines.append(f"  Size: {_size_label(self.size_percentile)}")
        lines += [
            f"  ROE avg: {_fmt(self.roe_avg)}  |  Net margin avg: {_fmt(self.net_margin_avg)}",
            f"  Gross margin trend (latest-oldest): {_fmt(self.gross_margin_trend)}",
            f"  Book value/share CAGR: {_fmt(self.bvps_cagr)}",
            f"  Debt/equity (latest): {_fmt(self.debt_to_equity_latest)}",
            "",
            "History (trailing-twelve-month periods, newest first):",
            "period | P/E | ROE | gross_m | op_m | net_m | D/E "
            "| curr | rev_gr | eps_idx | bvps_idx | fcf_idx | eps_yoy",
        ]
        for i, p in enumerate(self.periods):
            lines.append(
                f"t-{i} | {_fmt(p.price_to_earnings_ratio)} | {_fmt(p.return_on_equity)} "
                f"| {_fmt(p.gross_margin)} | {_fmt(p.operating_margin)} "
                f"| {_fmt(p.net_margin)} | {_fmt(p.debt_to_equity)} "
                f"| {_fmt(p.current_ratio)} | {_fmt(p.revenue_growth)} "
                f"| {_per_share_index(p.earnings_per_share, oldest.earnings_per_share)} "
                f"| {_per_share_index(p.book_value_per_share, oldest.book_value_per_share)} "
                f"| {_per_share_index(p.free_cash_flow_per_share, oldest.free_cash_flow_per_share)} "
                f"| {_fmt(self.eps_growth_yoy) if i == 0 else '-'}"
            )
        return "\n".join(lines)


def build_snapshot(
    ticker: str,
    as_of: str,
    data_client: DataClient,
    periods: int = 20,
    breakpoints: MEBreakpoints | None = None,
) -> FundamentalsSnapshot:
    """Build a snapshot for (ticker, as_of), retaining provider PIT metadata.

    `breakpoints`, when given, places the t-0 filed market cap on the size
    distribution of US stocks as of that filing (blind prompts show the
    percentile instead of dollars). Without it `size_percentile` stays None.

    Raises InsufficientData if fewer than MIN_PERIODS reporting periods exist.
    Data-layer failures propagate (fail loud) — a broken snapshot must never
    silently become a neutral view.
    """
    metrics = data_client.get_financial_metrics(
        ticker, as_of, period="ttm", limit=periods,
    )
    if len(metrics) < MIN_PERIODS:
        raise InsufficientData(
            f"{ticker} as of {as_of}: only {len(metrics)} reporting periods "
            f"(need {MIN_PERIODS})"
        )

    # Market cap comes from the most recent FILED metrics row. Deliberately
    # NOT data_client.get_market_cap(): that prefers company_facts.market_cap,
    # which is latest-only — lookahead in a backtest.
    facts = data_client.get_company_facts(ticker)

    rows = [
        PeriodFundamentals(**m.model_dump(include=set(PeriodFundamentals.model_fields)))
        for m in metrics
    ]

    size_percentile = None
    currency = metrics[0].currency or getattr(data_client, 'currency', None)
    if breakpoints is not None and currency and currency != breakpoints.currency:
        raise ValueError(f'market cap currency {currency} cannot be compared to {breakpoints.currency} breakpoints')
    if breakpoints is not None and rows[0].filing_date is not None:
        size_percentile = breakpoints.size_percentile(rows[0].market_cap, rows[0].filing_date)

    return FundamentalsSnapshot(
        ticker=ticker,
        as_of=as_of,
        point_in_time=getattr(data_client, "point_in_time", True),
        source=getattr(data_client, "provider", None),
        currency=currency,
        # Sector/industry are slow-moving company attributes; using latest
        # facts here is an accepted, documented PIT approximation.
        sector=facts.sector if facts else None,
        industry=facts.industry if facts else None,
        periods=rows,
        roe_avg=_avg([m.return_on_equity for m in metrics]),
        net_margin_avg=_avg([m.net_margin for m in metrics]),
        gross_margin_trend=_trend([m.gross_margin for m in metrics]),
        bvps_cagr=_cagr([m.book_value_per_share for m in metrics],
                        [m.report_period for m in metrics]),
        debt_to_equity_latest=metrics[0].debt_to_equity,
        market_cap_latest=metrics[0].market_cap,
        eps_growth_yoy=_eps_growth_yoy(rows),
        size_percentile=size_percentile,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _size_label(percentile: int) -> str:
    if percentile <= 0:
        return "market cap below the 5th percentile of US stocks"
    return f"market cap at or above the {percentile}th percentile of US stocks"


def _per_share_index(value: float | None, base: float | None) -> str:
    """Scale a per-share series to the oldest period (= 100).

    A missing or non-positive base has no scale, so the whole column is
    dashed rather than inventing one. The sign of `value` is kept, so a
    swing through zero still shows up.
    """
    if value is None or base is None or base <= 0:
        return "-"
    return f"{value / base * 100:.1f}"


def _eps_growth_yoy(periods: list[PeriodFundamentals]) -> float | None:
    """Compare the latest EPS with the same quarter of the previous year."""
    if not periods:
        return None
    latest_date = date.fromisoformat(periods[0].report_period[:10])
    quarter = (latest_date.month - 1) // 3
    latest = periods[0].earnings_per_share
    prior = None
    for period in periods[1:]:
        prior_date = date.fromisoformat(period.report_period[:10])
        if prior_date.year == latest_date.year - 1 and (prior_date.month - 1) // 3 == quarter:
            prior = period.earnings_per_share
            break
    if latest is None or prior is None or prior <= 0:
        return None
    return round(latest / prior - 1, 4)


def _fmt(v: float | None) -> str:
    if v is None:
        return "-"
    if abs(v) >= 1e9:
        return f"{v / 1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f}M"
    return f"{v:.2f}"


def _avg(values: list[float | None]) -> float | None:
    xs = [v for v in values if v is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _trend(values: list[float | None]) -> float | None:
    """Latest minus oldest (values arrive newest first)."""
    xs = [v for v in values if v is not None]
    return round(xs[0] - xs[-1], 4) if len(xs) >= 2 else None


def _cagr(values: list[float | None], report_periods: list[str] | None = None) -> float | None:
    """Annualize valid endpoint values over their actual elapsed dates.

    Direct callers without dates retain the quarter-grid assumption, keeping
    the original positions of missing values so gaps do not shorten time.
    """
    if report_periods is not None and len(values) != len(report_periods):
        raise ValueError("CAGR values and report periods must have the same length")
    valid = [(index, value) for index, value in enumerate(values) if value is not None]
    if len(valid) < 2:
        return None
    latest_index, latest = valid[0]
    oldest_index, oldest = valid[-1]
    if oldest <= 0 or latest <= 0:
        return None
    if report_periods is None:
        years = (oldest_index - latest_index) / 4
    else:
        latest_date = date.fromisoformat(report_periods[latest_index][:10])
        oldest_date = date.fromisoformat(report_periods[oldest_index][:10])
        years = (latest_date - oldest_date).days / 365.25
    if years <= 0:
        return None
    return round((latest / oldest) ** (1 / years) - 1, 4)
