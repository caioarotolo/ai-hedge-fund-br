"""Capture real REST contracts, validate snapshots, optionally replay a mandate.

No synthetic market data or LLM signals are used by this command.
Exit 2 means coverage/auth/schema/execution is still blocked; read the report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from hedge_fund.data import CachedDataClient, OpenMarketsClient
from hedge_fund.data.client import OpenMarketsError
from hedge_fund.data.client import normalize_ticker
from hedge_fund.data.sessions import completed_through
from hedge_fund.features.snapshot import build_snapshot
from hedge_fund.tui.keys import apply_credentials


def _safe_error(exc: Exception, openmarkets_key: str | None = None) -> str:
    """Keep useful adapter diagnostics while excluding bodies and credentials."""
    status = getattr(exc, 'status_code', None)
    if isinstance(exc, OpenMarketsError):
        detail = str(exc)
    elif isinstance(exc, ValueError) and str(exc) in {
        'empty coverage report',
        'benchmark coverage/price history is empty',
        'empty price coverage or incorrect source',
        'snapshot has no usable derived financial fields',
        'mandate benchmark must match the validated --benchmark',
        'all agents abstained; not a validated strategy replay',
    }:
        detail = str(exc)
    else:
        detail = type(exc).__name__
    secrets = [openmarkets_key]
    secrets.extend(os.getenv(name) for name in (
        'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'DEEPSEEK_API_KEY',
        'GOOGLE_API_KEY', 'GEMINI_API_KEY',
    ))
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, '[REDACTED]')
    if isinstance(status, int) and f'HTTP {status}' not in detail:
        detail = f'{detail} (HTTP {status})'
    return detail


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--universe', default='PETR4,VALE3,ITUB4')
    parser.add_argument('--benchmark', default='BOVA11')
    parser.add_argument('--start', default='2024-06-03')
    parser.add_argument('--end', default='2024-06-14')
    parser.add_argument('--out', default='outputs/openmarkets-validation')
    parser.add_argument('--mandate', help='also replay this mandate using actual agents')
    parser.add_argument('--model', help='existing LLM model for the mandate; does not change providers')
    args = parser.parse_args()
    apply_credentials()
    if args.model:
        os.environ['HEDGE_FUND_LLM_MODEL'] = args.model
    universe = list(dict.fromkeys(normalize_ticker(t) for t in args.universe.split(',')))
    benchmark = normalize_ticker(args.benchmark)
    if args.end > completed_through():
        parser.error('--end must refer to a completed Brazilian date')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {'source': 'openmarkets', 'base_url': OpenMarketsClient.BASE_URL,
              'time_utc': datetime.now(timezone.utc).isoformat(), 'currency': 'BRL',
              'historical_reliability': 'exploratory', 'universe_requested': universe,
              'universe_confirmed': [], 'checks': [], 'complete': False,
              'report_scope': {'requested': ['prices', 'fundamentals'],
                  'optional': ['mandate_replay'] if args.mandate else [],
                  'complete_means': 'all checks requested by this invocation passed'},
              'migration_complete': False,
              'unsupported_original_capabilities': [
                  'news_rest', 'earnings_announcements_eps_consensus_pead']}
    blocked = False
    request_artifacts_saved = 0
    with OpenMarketsClient() as raw:
        if not raw._api_key:
            report['checks'].append({'stage': 'authentication', 'ok': False,
                'error': 'OPENMARKETS_API_KEY missing in shell/local .env/saved .env'})
            blocked = True
        else:
            # Capture raw authenticated responses BEFORE adapter normalization.
            # These artifacts let a reviewer distinguish actual contracts from mocks.
            paths = [('/v1/metrics', {'period': 'ltm', 'frequency': 'period', 'limit': 100})]
            for ticker in universe:
                paths.extend([
                    (f'/v1/companies/{ticker}/coverage', {'period': 'ltm', 'frequency': 'period'}),
                    (f'/v1/companies/{ticker}', {}),
                    (f'/v1/quotes/{ticker}', {'start_date': args.start, 'end_date': args.end,
                      'latest_only': 'false', 'frequency': 'daily', 'price_basis': 'adjusted', 'limit': 1000}),
                ])
                if ticker in universe:
                    paths.extend([
                      (f'/v1/companies/{ticker}/financial-metrics', {'mode': 'history',
                        'period': 'ltm', 'frequency': 'period', 'end_date': args.end,
                        'consolidation': 'consolidado', 'limit': 1000}),
                      (f'/v1/companies/{ticker}/financial-statements', {'period': 'quarterly',
                        'statement_types': 'dre,bpa,bpp,dfc_mi', 'end_date': args.end,
                        'consolidation': 'consolidado', 'limit': 1000}),
                      (f'/v1/companies/{ticker}/proventos', {'start_date': args.start,
                        'end_date': args.end, 'summary': 'none', 'adjustment': 'raw', 'limit': 1000}),
                    ])
            if benchmark not in universe:
                # ETF prices can exist even when company-only routes are 404.
                paths.append((f'/v1/quotes/{benchmark}', {'start_date': args.start, 'end_date': args.end,
                    'latest_only': 'false', 'frequency': 'daily', 'price_basis': 'adjusted', 'limit': 1000}))
            for i, (path, params) in enumerate(paths):
                try:
                    response = raw._json(path, params)
                    # Never persist a credential even if an error/success echoes it.
                    serialized = json.dumps(response, ensure_ascii=False, indent=2).replace(raw._api_key, '[REDACTED]')
                    name = f'{i:02d}-' + path.strip('/').replace('/', '-') + '.json'
                    (out / name).write_text(serialized)
                    report['checks'].append({'stage': 'real_response', 'path': path,
                        'params': params, 'ok': True, 'artifact': name,
                        'sha256': hashlib.sha256(serialized.encode()).hexdigest()})
                    print(f'OpenMarkets response saved: {path}', flush=True)
                except Exception as exc:
                    blocked = True
                    report['checks'].append({'stage': 'real_response', 'path': path, 'ok': False,
                        'error': _safe_error(exc, raw._api_key)})
                    print(f'Blocked: {path}: {_safe_error(exc, raw._api_key)}', flush=True)
                    if getattr(exc, 'status_code', None) in (401, 403, 429):
                        break  # No quota waste or repeated auth failures.
            if not blocked:
                # Capture successful pages from adapter/backtest fetches before
                # normalization. The initial eight representative artifacts
                # above remain unchanged; this audit trail includes cursors.
                request_dir = out / 'requests'
                request_dir.mkdir(exist_ok=True)
                original_json = raw._json
                credentials = [raw._api_key]
                credentials.extend(os.getenv(name) for name in (
                    'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'DEEPSEEK_API_KEY',
                    'GOOGLE_API_KEY', 'GEMINI_API_KEY',
                ))

                def redact(value):
                    if isinstance(value, str):
                        for credential in credentials:
                            if credential:
                                value = value.replace(credential, '[REDACTED]')
                        return value
                    if isinstance(value, list):
                        return [redact(item) for item in value]
                    if isinstance(value, dict):
                        return {redact(key): redact(item) for key, item in value.items()}
                    return value

                def audited_json(path, params=None):
                    nonlocal request_artifacts_saved
                    body = original_json(path, params)
                    artifact = request_dir / (
                        f'{request_artifacts_saved:03d}-' + path.strip('/').replace('/', '-') + '.json'
                    )
                    payload = {'path': path, 'params': params or {}, 'response': body}
                    artifact.write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2))
                    request_artifacts_saved += 1
                    return body

                raw._json = audited_json
                data = CachedDataClient(raw, cache_dir=out / 'cache', refresh=True)
                try:
                    benchmark_prices = data.get_prices(benchmark, args.start, args.end)
                    if not benchmark_prices:
                        raise ValueError('benchmark coverage/price history is empty')
                    report['checks'].append({'stage': 'benchmark', 'ticker': benchmark,
                        'ok': True, 'bars': len(benchmark_prices),
                        'basis_confirmed_by_rows': all(p.price_basis_verified for p in benchmark_prices)})
                except Exception as exc:
                    blocked = True
                    report['checks'].append({'stage': 'benchmark', 'ticker': benchmark,
                        'ok': False, 'error': _safe_error(exc, raw._api_key)})
                for ticker in universe:
                    try:
                        coverage = raw.get_coverage(ticker)
                        if not coverage['data']:
                            raise ValueError('empty coverage report')
                        prices = data.get_prices(ticker, args.start, args.end)
                        snapshot = build_snapshot(ticker, args.end, data)
                        if not prices or any(p.data_source != 'openmarkets' for p in prices):
                            raise ValueError('empty price coverage or incorrect source')
                        finite_fields = ('roe_avg', 'net_margin_avg', 'bvps_cagr', 'eps_growth_yoy')
                        if not any(getattr(snapshot, field) is not None for field in finite_fields):
                            raise ValueError('snapshot has no usable derived financial fields')
                        (out / f'{ticker}-snapshot.json').write_text(snapshot.model_dump_json(indent=2))
                        report['universe_confirmed'].append(ticker)
                        report['checks'].append({'stage': 'snapshot', 'ticker': ticker, 'ok': True,
                            'bars': len(prices), 'periods': len(snapshot.periods), 'point_in_time': snapshot.point_in_time,
                            'basis_confirmed_by_rows': all(p.price_basis_verified for p in prices)})
                    except Exception as exc:
                        blocked = True
                        report['checks'].append({'stage': 'snapshot', 'ticker': ticker, 'ok': False,
                            'error': _safe_error(exc, raw._api_key)})
                if not blocked and args.mandate:
                    try:
                        from hedge_fund.backtesting import backtest_fund
                        from hedge_fund.fund import Fund, load_spec
                        spec = load_spec(args.mandate)
                        if spec.benchmark != benchmark:
                            raise ValueError('mandate benchmark must match the validated --benchmark')
                        fund = Fund(spec, blind=True)
                        replay_data = CachedDataClient(raw, cache_dir=out / 'cache')
                        replay_data.prefetch_history(universe, benchmark, args.start, args.end)
                        result = backtest_fund(fund, args.start, args.end, replay_data, universe)
                        signals = [s for r in result.records for st in (r.decision.strategies if r.decision else []) for s in st.signals]
                        if not signals or all(s.metadata.get('abstained') for s in signals):
                            raise ValueError('all agents abstained; not a validated strategy replay')
                        (out / 'backtest.json').write_text(result.model_dump_json(indent=2))
                        report['checks'].append({'stage': 'backtest', 'ok': True,
                            'sessions': len(result.dates), 'n_orders': result.metrics.n_orders,
                            'source': result.data_source, 'historical_reliability': result.historical_reliability})
                    except Exception as exc:
                        blocked = True
                        report['checks'].append({'stage': 'backtest', 'ok': False,
                            'error': _safe_error(exc, raw._api_key)})
        report['requests_made'] = raw.requests_made
    report['complete'] = not blocked
    report['backtest_requested'] = bool(args.mandate)
    report['request_artifacts_saved'] = request_artifacts_saved
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f'Report: {out / "report.json"}', flush=True)
    return 2 if blocked else 0


if __name__ == '__main__':
    raise SystemExit(main())
