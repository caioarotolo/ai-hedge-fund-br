"""OpenMarkets REST adapter. No fallback to another market-data provider.

The legacy FDClient names remain import aliases for downstream code only.
Missing capabilities and malformed responses fail loudly, never as no signal.
"""
from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

from hedge_fund.data.models import CompanyFacts, FinancialMetrics, InsiderTrade, Price

logger = logging.getLogger(__name__)


class OpenMarketsError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, path: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.path = path


class CoverageError(OpenMarketsError):
    """A requested capability/field is unavailable or unverified."""


# Canonical OpenMarkets metric_key values observed in the REST contract, mapped
# only where their definitions match a normalized FinancialMetrics field.
# LTM metrics are explicitly namespaced by the provider; unsupported values
# (notably quarterly EPS/FCF per share in an LTM request) remain None.
METRIC_MAP = {
    'market_cap': 'market_cap',
    'enterprise_value': 'enterprise_value',
    'pe_ratio': 'price_to_earnings_ratio',
    'pb_ratio': 'price_to_book_ratio',
    'price_to_revenue': 'price_to_sales_ratio',
    'ev_to_ebitda': 'enterprise_value_to_ebitda_ratio',
    'ev_to_revenue': 'enterprise_value_to_revenue_ratio',
    'fcf_yield': 'free_cash_flow_yield',
    'peg_ratio': 'peg_ratio',
    'asset_turnover': 'asset_turnover',
    'current_ratio': 'current_ratio',
    'quick_ratio': 'quick_ratio',
    'revenue_growth': 'revenue_growth',
    'bvps': 'book_value_per_share',
    'ltm:roe': 'return_on_equity',
    'ltm:roa': 'return_on_assets',
    'ltm:roic': 'return_on_invested_capital',
    'ltm:gross_margin': 'gross_margin',
    'ltm:ebit_margin': 'operating_margin',
    'ltm:net_margin': 'net_margin',
    'ltm:interest_coverage': 'interest_coverage',
    'eps': 'earnings_per_share',
    'fcf_per_share': 'free_cash_flow_per_share',
}
_NON_TTM_METRICS = frozenset({'eps', 'fcf_per_share'})
MONEY = {'market_cap', 'enterprise_value', 'earnings_per_share',
         'book_value_per_share', 'free_cash_flow_per_share'}
FRACTIONS = {
    'gross_margin', 'operating_margin', 'net_margin', 'return_on_equity',
    'return_on_assets', 'return_on_invested_capital', 'free_cash_flow_yield',
    'revenue_growth', 'earnings_growth', 'book_value_growth', 'earnings_per_share_growth',
    'free_cash_flow_growth', 'operating_income_growth', 'ebitda_growth', 'payout_ratio',
}


def normalize_ticker(ticker: str) -> str:
    ticker = ticker.strip().upper()
    if ticker.endswith('.SA'):
        ticker = ticker[:-3]
    if not re.fullmatch(r'[A-Z]{4}\d{1,2}', ticker):
        raise CoverageError(f'{ticker!r}: expected a B3 ticker, e.g. PETR4 or BOVA11')
    return ticker


def _day(value: str) -> str:
    return date.fromisoformat(value[:10]).isoformat()


class OpenMarketsClient:
    BASE_URL = 'https://api.openmarkets.com.br'
    source = provider = 'openmarkets'
    currency = 'BRL'
    point_in_time = False  # No original-publication/versioned contract in REST docs.
    earnings_surprises = False
    price_basis = 'adjusted'
    cache_namespace = 'openmarkets-rest-v1-adapter-3-adjusted-restated'
    _RETRY_DELAYS = (6, 15, 30)
    _rate_lock = threading.Lock()
    _last_request = 0.0

    def __init__(self, api_key: str | None = None, timeout: float = 30.0,
                 *, request_interval: float = 6.1):
        # Same precedence as CLI: shell > checkout .env > saved user .env.
        load_dotenv(Path.cwd() / '.env', override=False)
        from hedge_fund.paths import ENV_PATH
        load_dotenv(ENV_PATH, override=False)
        self._api_key = api_key if api_key is not None else os.getenv('OPENMARKETS_API_KEY', '')
        self._timeout = timeout
        self._request_interval = request_interval
        self._session = requests.Session()
        self._session.headers.update({'Authorization': f'Bearer {self._api_key}', 'Accept': 'application/json'})
        self._catalog = None
        self.response_metadata = {}
        self.normalization_audit = {}
        self._warned_history = False
        self.requests_made = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._session.close()

    def _request(self, method: str, path: str, **kwargs):
        if not self._api_key:
            raise OpenMarketsError('OPENMARKETS_API_KEY is missing; configure the local .env', path=path)
        # Cursors are opaque query values, never URLs. Do not forward a secret
        # to redirects or a host supplied by an API response.
        if not path.startswith('/v1/') or '?' in path or '..' in path:
            raise OpenMarketsError('Invalid OpenMarkets endpoint', path=path)
        for delay in (*self._RETRY_DELAYS, None):
            with self._rate_lock:
                wait = self._request_interval - (time.monotonic() - OpenMarketsClient._last_request)
                if wait > 0:
                    time.sleep(wait)
                OpenMarketsClient._last_request = time.monotonic()
            try:
                self.requests_made += 1
                response = self._session.request(method, self.BASE_URL + path,
                    timeout=self._timeout, allow_redirects=False, **kwargs)
            except requests.RequestException:
                # Exception/body/headers may contain credentials. Never echo them.
                raise OpenMarketsError(f'{method} {path}: transport failure', path=path) from None
            if response.status_code == 429 and delay is not None:
                retry = getattr(response, 'headers', {}).get('Retry-After', '')
                try:
                    delay = min(60.0, max(delay, float(retry)))
                except ValueError:
                    pass
                time.sleep(delay)
                continue
            if response.status_code >= 300:
                raise OpenMarketsError(f'{method} {path}: HTTP {response.status_code}',
                    status_code=response.status_code, path=path)
            return response

    def _json(self, path: str, params: dict | None = None) -> dict:
        try:
            body = self._request('GET', path, params=params or {}).json()
        except ValueError:
            raise OpenMarketsError(f'{path}: invalid JSON', path=path) from None
        if not isinstance(body, dict) or 'data' not in body or body.get('error'):
            raise OpenMarketsError(f'{path}: invalid REST envelope (expected data)', path=path)
        self.response_metadata[path] = body.get('meta', {})
        return body

    def _pages(self, path: str, params: dict, *, max_rows: int | None = None) -> list[dict]:
        rows, seen = [], set()
        query = dict(params)
        quote_basis = None
        for _ in range(1000):
            body = self._json(path, query)
            page = body['data']
            # Only a list contract is accepted; an unknown shape is not no-data.
            if not isinstance(page, list) or any(not isinstance(r, dict) for r in page):
                raise CoverageError(f'{path}: unverified row schema; expected data[]', path=path)
            rows.extend(page)
            meta = body.get('meta', {})
            if not isinstance(meta, dict):
                raise OpenMarketsError(f'{path}: invalid pagination metadata', path=path)
            if path.startswith('/v1/quotes/'):
                basis = (meta.get('price_basis'), meta.get('ohlc_price_basis'))
                if quote_basis is not None and basis != quote_basis:
                    raise CoverageError(f'{path}: price basis changed between pages', path=path)
                quote_basis = basis
            cursor = meta.get('next_cursor')
            if not cursor:
                if meta.get('truncated') is True:
                    raise OpenMarketsError(f'{path}: truncated page has no continuation cursor', path=path)
                return rows[:max_rows] if max_rows is not None else rows
            if not isinstance(cursor, str) or cursor in seen:
                raise OpenMarketsError(f'{path}: invalid/repeated pagination cursor', path=path)
            if max_rows is not None and len(rows) >= max_rows:
                return rows[:max_rows]
            seen.add(cursor)
            query['cursor'] = cursor
        raise OpenMarketsError(f'{path}: pagination exceeded 1000 pages', path=path)

    def get_coverage(self, ticker: str) -> dict:
        return self._json(f'/v1/companies/{normalize_ticker(ticker)}/coverage',
                          {'period': 'ltm', 'frequency': 'period'})

    def search_companies(self, query: str) -> list[dict]:
        return self._pages('/v1/companies/search', {'q': query, 'limit': 25})

    def get_metric_catalog(self) -> list[dict]:
        if self._catalog is None:
            self._catalog = self._pages('/v1/metrics', {'period': 'ltm', 'frequency': 'period', 'limit': 100})
        return self._catalog

    def get_prices(self, ticker: str, start_date: str, end_date: str,
                   interval: str = 'day', interval_multiplier: int = 1) -> list[Price]:
        ticker = normalize_ticker(ticker)
        start, end = _day(start_date), _day(end_date)
        if start > end:
            raise ValueError('start_date must precede end_date')
        if interval != 'day' or interval_multiplier != 1:
            raise CoverageError('Only daily single-session OHLCV is supported by this adapter')
        path = f'/v1/quotes/{ticker}'
        rows = self._pages(path, {'start_date': start, 'end_date': end, 'latest_only': 'false',
            'frequency': 'daily', 'price_basis': self.price_basis, 'limit': 1000})
        meta = self.response_metadata.get(path, {})
        bars = {}
        for row in rows:
            try:
                if 'trade_date' in row:
                    if row.get('symbol') != ticker:
                        raise ValueError('quote symbol mismatch')
                    day = _day(row['trade_date'])
                    values = {key: float(row[key + '_price']) for key in ('open', 'high', 'low', 'close')}
                    values['volume'] = float(row['tick_volume'])
                    raw_close = values['close']
                    if meta.get('price_basis') != self.price_basis or meta.get('ohlc_price_basis') != 'raw':
                        raise ValueError('unverified quote basis')
                    selected = float(row['price'])
                    adjustment = float(row['adjusted_close_price']) if self.price_basis == 'adjusted' else raw_close
                    if not math.isclose(selected, adjustment, rel_tol=1e-8):
                        raise ValueError('selected price disagrees with requested basis')
                    factor = adjustment / raw_close
                    # Raw OHLC are real observations; put every price on the
                    # adjusted close's scale using the provider's daily factor.
                    # Volume stays on its original provider scale.
                    for key in ('open', 'high', 'low', 'close'):
                        values[key] *= factor
                    basis_verified = True
                    derived = self.price_basis == 'adjusted'
                else:
                    day = _day(row.get('date') or row['time'])
                    values = {key: float(row[key]) for key in ('open', 'high', 'low', 'close', 'volume')}
                    raw_close, factor, derived = None, None, False
                    basis_verified = row.get('price_basis') == self.price_basis
                # Do not manufacture OHLC or volume from a close-only series.
                if any(v is None or not math.isfinite(float(v)) for v in values.values()):
                    raise ValueError('nonfinite OHLCV')
                if any(float(values[k]) <= 0 for k in ('open','high','low','close')):
                    raise ValueError('nonpositive price')
                if float(values['volume']) < 0 or not float(values['volume']).is_integer():
                    raise ValueError('invalid volume')
                if values['low'] > min(values['open'], values['close']) or values['high'] < max(values['open'], values['close']):
                    raise ValueError('invalid OHLC range')
                if row.get('currency', 'BRL') != 'BRL' or row.get('price_basis', self.price_basis) != self.price_basis:
                    raise ValueError('wrong price basis/currency')
                bar = Price(**values, time=day, currency='BRL', data_source=self.source, price_basis=self.price_basis,
                            price_basis_verified=basis_verified, raw_close=raw_close,
                            adjustment_factor=factor, adjusted_ohlc_derived=derived,
                            underlying_source=row.get('source'))
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                raise CoverageError(f'{path}: incomplete or invalid daily OHLCV contract', path=path) from None
            if start <= day <= end:
                if day in bars and bars[day] != bar:
                    raise CoverageError(f'{path}: conflicting bars for {day}', path=path)
                bars[day] = bar
        return [bars[day] for day in sorted(bars)]

    def get_financial_metrics(self, ticker: str, end_date: str, period: str = 'ttm', limit: int = 10) -> list[FinancialMetrics]:
        ticker, end = normalize_ticker(ticker), _day(end_date)
        if period not in {'ttm', 'quarterly', 'annual'} or limit < 1:
            raise ValueError('period must be ttm/quarterly/annual and limit positive')
        if not self._warned_history:
            logger.warning('OpenMarkets: exploratory fundamentals; original publication dates and revision history are unverified')
            self._warned_history = True
        path = f'/v1/companies/{ticker}/financial-metrics'
        api_period = 'ltm' if period == 'ttm' else period
        period_map = {(key.removeprefix('ltm:') if period != 'ttm' else key): field
                      for key, field in METRIC_MAP.items()}
        # The REST contract's metric_keys filter rejects some keys emitted by
        # financial history (for example, price_to_revenue). Fetch the valid
        # unfiltered history and retain only METRIC_MAP keys below. `limit` is
        # a page size, not the number of periods, so continue through all pages.
        rows = self._pages(path, {'mode': 'history', 'period': api_period,
            'frequency': 'period', 'end_date': end, 'consolidation': 'consolidado',
            'market_date_mode': 'statement_date', 'limit': 1000})
        if not rows:
            return []

        grouped: dict[str, dict[str, dict]] = {}
        ratio_units = {'ratio', 'fraction', 'decimal', 'dimensionless', '1',
                       '%', 'percent', 'percentage', 'pct', 'multiple', 'x', 'times'}
        currency_units = {'BRL', 'BRL thousands', 'BRL_thousands', 'mil BRL',
                          'BRL millions', 'BRL_millions'}
        share_units = {'BRL/share', 'BRL/ação'}
        semantic_units = {
            'currency': currency_units,
            'currency_per_share': share_units,
            'ratio': ratio_units,
        }
        for row in rows:
            if not isinstance(row, dict):
                raise CoverageError(f'{path}: unverified metric row schema', path=path)
            try:
                key = row['metric_key']
                if not isinstance(key, str):
                    raise ValueError('metric_key must be a string')
            except (KeyError, ValueError, TypeError):
                raise CoverageError(f'{path}: unverified metric history schema (metric_key)', path=path) from None
            field = period_map.get(key)
            if field is None or (period == 'ttm' and key in _NON_TTM_METRICS):
                continue
            try:
                report = _day(row.get('period_end') or row.get('report_period') or row['date'])
                row_period = row.get('period')
                if row_period is not None and row_period != api_period:
                    raise ValueError('mixed periods')
            except (KeyError, ValueError, TypeError):
                raise CoverageError(
                    f'{path}: unverified metric history schema (metric_key, period_end)',
                    path=path,
                ) from None
            if report > end:
                continue
            raw_delivery = row.get('source_delivery_date')
            if raw_delivery in (None, ''):
                # Older provider rows may have no delivery date. Do not infer
                # availability or manufacture a filing date for the cutoff.
                continue
            try:
                delivery = _day(raw_delivery)
            except (ValueError, TypeError):
                raise CoverageError(
                    f'{path}: unverified metric history schema (source_delivery_date)',
                    path=path,
                ) from None
            if delivery > end:
                continue
            if period == 'quarterly' and key in _NON_TTM_METRICS:
                # ITR per-share facts may be YTD even in a quarterly response.
                # Do not subtract per-share YTD values with potentially
                # different denominators or label them an isolated quarter.
                report_day = date.fromisoformat(report)
                quarter_start = report_day.replace(month=((report_day.month - 1) // 3) * 3 + 1, day=1)
                if not row.get('period_start') or _day(row['period_start']) != quarter_start.isoformat():
                    continue
            if 'value' not in row:
                raise CoverageError(f'{path}: {field} has no value field', path=path)

            raw_currency = row.get('currency', 'BRL')
            if raw_currency not in ('BRL', 'REAL'):
                raise CoverageError(f'{path}: non-BRL financial value', path=path)
            currency = 'BRL'  # OpenMarkets emits REAL for Brazilian-real rows.
            availability = row.get('availability')
            available = row.get('available')
            if available is not None and not isinstance(available, bool):
                raise CoverageError(f'{path}: invalid metric available flag for {key}', path=path)
            if available is False:
                # `availability` is an extensible reason enum. The explicit
                # false flag is sufficient to mark the value unavailable.
                value = None
            elif availability in ('not_applicable', 'missing_inputs'):
                value = None
            else:
                if availability not in (None, 'available'):
                    raise CoverageError(f'{path}: unrecognized metric availability for {key}', path=path)
                value = row['value']

            raw_version = row.get('version', 0)
            if isinstance(raw_version, bool):
                raise CoverageError(f'{path}: invalid version for {key}', path=path)
            try:
                version = int(raw_version)
            except (TypeError, ValueError):
                raise CoverageError(f'{path}: invalid version for {key}', path=path) from None
            if version < 0:
                raise CoverageError(f'{path}: invalid version for {key}', path=path)

            if value is not None:
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    raise CoverageError(f'{path}: {field} has a nonnumeric value', path=path) from None
                if not math.isfinite(value):
                    raise CoverageError(f'{path}: {key} has nonfinite value', path=path)

                # `output_unit` is a semantic category (`currency`, `ratio`,
                # `currency_per_share`); `unit` carries the physical unit.
                # Prefer the row's physical unit and use the category only as
                # a safe fallback. REAL is the API's currency code for BRL.
                unit = row.get('unit')
                output_unit = row.get('output_unit')
                if not unit:
                    unit = {
                        'currency': 'BRL',
                        'currency_per_share': 'BRL/share',
                        'ratio': 'ratio',
                    }.get(output_unit)
                if not unit:
                    raise CoverageError(f'{path}: {key} has no verifiable unit', path=path)
                allowed_for_output = semantic_units.get(output_unit)
                if allowed_for_output is not None and unit not in allowed_for_output:
                    raise CoverageError(f'{path}: {key} unit conflicts with output_unit', path=path)

                if field in MONEY and unit not in currency_units | share_units:
                    raise CoverageError(f'{path}: monetary unit unverified for {field}', path=path)
                if field in FRACTIONS:
                    if unit in ('%', 'percent', 'percentage', 'pct'):
                        value /= 100
                    elif unit not in ('ratio', 'fraction', 'decimal', 'dimensionless', '1'):
                        raise CoverageError(f'{path}: {key} unit {unit!r} unverified; refusing implicit percent conversion', path=path)
                elif unit in ('BRL thousands', 'BRL_thousands', 'mil BRL'):
                    if row.get('value_scale') != 'normalized_from_thousands':
                        value *= 1000
                elif unit in ('BRL millions', 'BRL_millions'):
                    value *= 1_000_000
                elif unit not in currency_units | share_units | ratio_units | {'days', None}:
                    raise CoverageError(f'{path}: {key} unit {unit!r} unsupported', path=path)

            candidate = {'value': value, 'metric_key': key, 'version': version,
                         'source_delivery_date': delivery}
            fields = grouped.setdefault(report, {})
            previous = fields.get(field)
            if previous is not None:
                if previous['metric_key'] != key:
                    raise CoverageError(
                        f'{path}: ambiguous metric keys for {field} at {report}', path=path)
                candidate_rank = (version, delivery)
                previous_rank = (previous['version'], previous['source_delivery_date'])
                if candidate_rank == previous_rank and candidate['value'] != previous['value']:
                    raise CoverageError(f'{path}: conflicting {key} version {version} for {report}', path=path)
                if candidate_rank > previous_rank:
                    fields[field] = candidate
            else:
                fields[field] = candidate

        result = []
        for report in sorted(grouped, reverse=True)[:limit]:
            candidates = grouped[report]
            record = {
                'ticker': ticker,
                'report_period': report,
                'period': period,
                'currency': 'BRL',
                'data_source': self.source,
                'point_in_time': False,
                'filing_date': max(
                    (item['source_delivery_date'] for item in candidates.values()
                     if item['value'] is not None),
                    default=None,
                ),
            }
            record.update({field: item['value'] for field, item in candidates.items()})
            result.append(FinancialMetrics(**record))
        return result

    def get_company_facts(self, ticker: str) -> CompanyFacts | None:
        ticker = normalize_ticker(ticker)
        path = f'/v1/companies/{ticker}'
        row = self._json(path)['data']
        if row is None:
            return None
        if not isinstance(row, dict) or not (row.get('name') or row.get('company_name')):
            raise CoverageError(f'{path}: unverified company profile schema', path=path)
        return CompanyFacts(ticker=ticker, name=row.get('name') or row['company_name'],
            sector=row.get('sector'), industry=row.get('industry') or row.get('segment'), exchange='B3', location='Brazil',
            is_active=row.get('b3_status') == 'A' if 'b3_status' in row else row.get('is_active', True))

    def get_market_cap(self, ticker: str, end_date: str) -> float | None:
        # Latest profile values cannot be used as historical market cap.
        rows = self.get_financial_metrics(ticker, end_date, limit=1)
        return rows[0].market_cap if rows else None

    def get_proventos(self, ticker: str, start_date: str, end_date: str) -> list[dict]:
        return self._pages(f'/v1/companies/{normalize_ticker(ticker)}/proventos',
            {'start_date': _day(start_date), 'end_date': _day(end_date), 'date_basis': 'ex_date',
             'summary': 'none', 'adjustment': 'raw', 'limit': 1000})

    def get_news(self, ticker, end_date, start_date=None, limit=1000):
        raise CoverageError('OpenMarkets REST has no news endpoint in the published 22-route catalog', path='/news/')

    def get_insider_trades(self, ticker, end_date, start_date=None, limit=1000):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError('limit must be a positive integer')
        ticker, end = normalize_ticker(ticker), _day(end_date)
        path = '/v1/governance/insiders'
        params = {'ticker': ticker, 'period': 'all', 'view': 'movements', 'limit': 1000,
                  'end_date': end}
        if start_date is not None:
            start_date = _day(start_date)
            # The API filters the reference month, not the movement's day.
            params['start_date'] = start_date[:7] + '-01'
        result = []
        rows = self._pages(path, params)
        summaries, outside = 0, 0
        for row in rows:
            try:
                month = _day(row['referencia_data'])
                day = row['dia']
                if day is None:
                    # The real movements view also returns monthly holdings
                    # summaries explicitly labeled "sem_mov". They report no
                    # transaction; neither invent a day nor call them trades.
                    if row.get('operacao') == 'sem_mov' and all(
                        row.get(k) is None for k in ('quantidade', 'preco', 'volume_reais')
                    ):
                        summaries += 1
                        continue
                    raise ValueError('undated movement')
                transaction = date.fromisoformat(month).replace(day=int(day)).isoformat()
                if not ((start_date or '0001-01-01') <= transaction <= end):
                    outside += 1
                    continue
                if row.get('moeda') not in ('BRL', 'REAL'):
                    raise ValueError('unknown currency')
                # CVM Art. 11 can aggregate a governing body. Its issuer name
                # is not an individual insider's name. Publication is unknown.
                def number(key):
                    value = row.get(key)
                    if value is None:
                        return None
                    value = float(value)
                    if not math.isfinite(value):
                        raise ValueError('nonfinite movement')
                    return value
                result.append(InsiderTrade(ticker=ticker, ticker_scope='reporting_company', name=None, filing_date=None,
                    point_in_time=False, is_aggregated=row.get('escopo') == 'consolidada',
                    origin=row.get('origem'), reference_date=month, data_source=self.source,
                    issuer=row.get('entidade_nome'), transaction_date=transaction,
                    transaction_type=row.get('operacao'), security_title=row.get('instrument_label'),
                    instrument_class=row.get('instrument_class'), transaction_units=number('quantidade'),
                    transaction_price_per_unit=number('preco'),
                    transaction_shares=number('quantidade') if row.get('instrument_class') == 'local_share' else None,
                    transaction_price_per_share=number('preco') if row.get('instrument_class') == 'local_share' else None,
                    transaction_value=number('volume_reais'),
                    shares_owned_before_transaction=number('saldo_inicial'),
                    shares_owned_after_transaction=number('saldo_final')))
            except (KeyError, ValueError, TypeError):
                raise CoverageError(f'{path}: invalid dated movement contract', path=path) from None
        self.normalization_audit[path] = {'raw_rows': len(rows), 'non_movement_monthly_summaries': summaries,
            'outside_transaction_date_window': outside, 'dated_movements_in_window': len(result),
            'publication_dates_available': False, 'ticker_scope': 'reporting_company'}
        return sorted(result, key=lambda r: r.transaction_date, reverse=True)[:limit]

    def _statement_rows(self, ticker, limit):
        from hedge_fund.data.sessions import completed_through
        from hedge_fund.data.statements import STATEMENT_CANONICALS
        ticker = normalize_ticker(ticker)
        end = completed_through()
        # Include a preceding annual/Q3 cohort to isolate Q4 and YTD flows.
        years = max(2, math.ceil((limit + 4) / 4))
        start = f'{date.fromisoformat(end).year - years:04d}-01-01'
        path = f'/v1/companies/{ticker}/financial-statements'
        params = {'start_date': start, 'end_date': end, 'consolidation': 'consolidado',
                  'canonical_names': ','.join(sorted(STATEMENT_CANONICALS)), 'limit': 1000}
        quarterly = self._pages(path, {**params, 'period': 'quarterly'})
        annual = self._pages(path, {**params, 'period': 'annual'})
        return ticker, quarterly, annual

    def get_earnings(self, ticker):
        from hedge_fund.data.statements import normalize_statements
        ticker, quarterly, annual = self._statement_rows(ticker, 1)
        try:
            return normalize_statements(ticker, quarterly, annual)
        except ValueError as exc:
            raise CoverageError(f'financial-statements: {exc}',
                                path=f'/v1/companies/{ticker}/financial-statements') from None

    def get_earnings_history(self, ticker, limit=12):
        from hedge_fund.data.statements import normalize_statement_history
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError('limit must be a positive integer')
        ticker, quarterly, annual = self._statement_rows(ticker, limit)
        try:
            return normalize_statement_history(ticker, quarterly, annual, limit)
        except ValueError as exc:
            raise CoverageError(f'financial-statements: {exc}',
                                path=f'/v1/companies/{ticker}/financial-statements') from None


# Compatibility imports; both names exclusively use OpenMarkets.
FDClient = OpenMarketsClient
FDClientError = OpenMarketsError
