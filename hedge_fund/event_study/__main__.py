"""Fail explicitly when OpenMarkets cannot supply earnings surprise events."""

from __future__ import annotations

import sys

TICKERS = ["PETR4", "VALE3", "ITUB4", "BBAS3", "ABEV3"]
MARKET_TICKER = "BOVA11"
CURRENCY = "BRL"


def main() -> None:
    print(
        "Estudo de eventos de earnings indisponível com OpenMarkets: faltam "
        "surpresas EPS e datas verificadas de anúncio para definir os eventos. "
        "Não houve cálculo de CAR nem inferência de eventos a partir da data "
        "de encerramento do trimestre. Veja docs/openmarkets-backtesting.md.",
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
