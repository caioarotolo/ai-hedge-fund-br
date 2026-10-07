"""Validate statement views, insider movements and distributions against REST.

These checks require access to the relevant OpenMarkets datasets. Statement
views have no announcement/consensus contract and remain unsuitable for PEAD.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hedge_fund.data import OpenMarketsClient
from hedge_fund.validate_openmarkets import _safe_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker', default='PETR4')
    parser.add_argument('--start', default='2024-01-01')
    parser.add_argument('--end', default='2024-06-14')
    parser.add_argument('--out', default='outputs/openmarkets-extended')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {'data_source': 'openmarkets', 'currency': 'BRL', 'checks': [],
              'report_scope': 'latest statement views; dated insider movements and distributions',
              'historical_reliability': 'exploratory', 'migration_complete': False,
              'unsupported_original_capabilities': ['news_rest', 'earnings_announcements_eps_consensus_pead'],
              'complete': False}
    with OpenMarketsClient() as client:
        methods = [('get_earnings', (args.ticker,)),
                   ('get_earnings_history', (args.ticker, 12)),
                   ('get_insider_trades', (args.ticker, args.end, args.start, 1000)),
                   ('get_proventos', (args.ticker, args.start, args.end))]
        for method, values in methods:
            try:
                result = getattr(client, method)(*values)
                data = ([r.model_dump() if hasattr(r, 'model_dump') else r for r in result]
                        if isinstance(result, list) else result.model_dump() if result else None)
                if not data:
                    raise ValueError('empty coverage report')
                serialized = json.dumps(data, ensure_ascii=False, indent=2).replace(client._api_key, '[REDACTED]')
                name = f'{method}.json'
                (out / name).write_text(serialized)
                report['checks'].append({'method': method, 'ok': True, 'artifact': name,
                                        'rows': len(data) if isinstance(data, list) else 1})
                print(f'{method}: OK', flush=True)
            except Exception as exc:
                report['checks'].append({'method': method, 'ok': False,
                    'error': _safe_error(exc, client._api_key),
                    'status_code': getattr(exc, 'status_code', None), 'path': getattr(exc, 'path', None)})
                print(f'{method}: FAILED ({type(exc).__name__})', flush=True)
        report['requests_made'] = client.requests_made
        report['normalization_audit'] = client.normalization_audit
    report['complete'] = all(check['ok'] for check in report['checks'])
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
