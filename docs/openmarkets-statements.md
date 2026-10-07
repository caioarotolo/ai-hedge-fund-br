# OpenMarkets financial statements → Earnings

`hedge_fund.data.statements.normalize_statements(ticker, quarterly_rows,
annual_rows) -> Earnings | None` maps the `data` arrays from quarterly and
annual `/v1/companies/{ticker}/financial-statements` responses into the
existing latest-period `Earnings` model. It is a pure local transformation;
it makes no API calls.

`normalize_statement_history(ticker, quarterly_rows, annual_rows, limit=12)`
returns period records for statement-history consumers. Quarterly periods are
tagged `source_type="ITR"`; annual periods are `source_type="DFP"`. Q4 flow
values on a DFP year-end record are included only when they are supported by an
explicit Q4 fact or a compatible annual-minus-Q3-YTD calculation. End-of-year
balance-sheet snapshots may still appear on that quarterly view. Both helpers
leave `filing_date`, `filing_datetime`, and `filing_window` null; these records
must not be treated as dated earnings announcements or PEAD inputs.
Both output models set `data_source="openmarkets"` and `point_in_time=False`;
the provider-independent model defaults remain `"unspecified"` and `True` for
other callers.

Money facts with `currency=REAL` and `currency_scale=MIL` are converted from
BRL thousands into absolute BRL by multiplying by 1,000. Other currency or
scale values raise `ValueError`. The normalizer prefers consolidated facts,
then ITR for quarterly pages and DFP for annual pages when both source datasets
are present for the same period. It keeps only one dataset/consolidation cohort
per period, chooses the greatest fact `version`, collapses equal duplicates,
and raises on conflicting facts at the same latest version.

Mapped canonical facts are:

| Earnings field | OpenMarkets canonical facts |
|---|---|
| `revenue` | `revenue` |
| `net_income` | `net_income`, then `net_income_common` as a fallback |
| `gross_profit` | `gross_profit` |
| `operating_income` | `operating_income`, then `ebit` |
| `net_cash_flow_from_operations` | `operating_cash_flow` |
| `capital_expenditure` | `capex` |
| `net_cash_flow_from_investing` | `investing_cash_flow` |
| `net_cash_flow_from_financing` | `financing_cash_flow` when supplied |
| `cash_and_equivalents` | `cash_and_equivalents` |
| `total_assets` | `total_assets` |
| `shareholders_equity` | `shareholders_equity`, then `equity` |
| `total_debt` | `total_debt`, or the sum of both short- and long-term debt |
| `total_liabilities` | `total_liabilities`, or current plus non-current liabilities |
| `free_cash_flow` | `free_cash_flow`, or operating cash flow plus signed capex |

Quarterly income and cash-flow facts marked `quarterly_standalone` are used as
reported. For YTD facts, Q1 is the YTD value; Q2/Q3 is the difference from the
immediately previous quarter's YTD fact, only when both are calendar-year
facts from the same dataset and consolidation scope with a Jan 1 start. A Q4
flow is derived from annual minus same-year Q3 YTD only when Q3 YTD starts Jan 1
and the annual period end is Dec 31; an annual `period_start`, when supplied,
must agree. An explicit standalone Q4 fact takes precedence.
Balance-sheet facts are snapshots and are never differenced. When the latest
annual date is a calendar Dec 31 later than the latest ITR quarter, that date
can represent latest-quarter Q4: annual balance-sheet facts are used, while
quarterly flows remain `None` unless the Q4 calculation is supported.

Missing facts stay `None`. The mapping does not infer EPS from net income or
share counts, and does not populate weighted average shares, consensus,
surprises, filing dates, or announcement dates. Consequently, normalized
`Earnings` is suitable only as a latest financial-statement view; it is not an
earnings-event record for PEAD or event studies.

The captured PETR4 response was a truncated first page with a 1,000-row limit.
It demonstrates the row shape and the observed ITR canonical names, not full
historical coverage or a stable account catalog. The REST contract does not
provide complete point-in-time publication metadata or original versions;
current facts may include later restatements. `report_period` is the statement
period end, never an announcement or filing date, and the result must be treated
as non-point-in-time. Unknown units, malformed periods, and ambiguous duplicate
facts raise `ValueError` instead of producing guessed financial values.

An offline replay of the saved first page produced latest period `2024-03-31`
(`Q1 2024`) with 13 of 27 `EarningsData` fields populated: revenue, net income,
gross profit, operating income, free cash flow, cash, total debt, assets,
liabilities, equity, operating/investing cash flow, and capex. No annual rows
were passed to that replay, so its annual view had 0 of 27 fields; this is not
evidence that the annual endpoint lacks coverage.
