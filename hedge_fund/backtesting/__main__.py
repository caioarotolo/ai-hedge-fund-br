"""Explain the unsupported PEAD demo instead of reporting a zero-trade success.

Use ``aihf backtest`` with a fundamentals-only mandate for supported research.
The OpenMarkets adapter has no documented earnings-consensus endpoint.
"""

from __future__ import annotations

import sys

TICKERS = ["PETR4", "VALE3", "ITUB4", "BBAS3", "ABEV3"]
CURRENCY = "BRL"


def main() -> None:
    print(
        "PEAD indisponível com OpenMarkets: faltam consenso de EPS, surpresa "
        "BEAT/MISS e data do anúncio dos resultados. DRE/ITR/DFP não substituem "
        "esse contrato. Nenhum backtest PEAD foi executado. Use `aihf backtest` "
        "com um mandato sem `pead`; veja docs/openmarkets-backtesting.md.",
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
