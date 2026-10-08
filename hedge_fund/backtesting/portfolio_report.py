from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np


def risk_metrics(returns: list[float], benchmark: list[float]) -> dict:
    r, b = np.asarray(returns, dtype=float), np.asarray(benchmark, dtype=float)
    n = len(r)
    vol = float(r.std(ddof=1)) if n > 1 else 0.0
    bvol = float(b.std(ddof=1)) if n > 1 else 0.0
    mean = float(r.mean()) if n else 0.0
    beta = float(np.cov(r, b, ddof=1)[0, 1] / bvol ** 2) if bvol > 1e-12 else None
    downside = float(np.sqrt(np.mean(np.minimum(r, 0) ** 2))) if n else 0.0
    active = r - b
    tracking = float(active.std(ddof=1)) if n > 1 else 0.0
    cutoff = float(np.quantile(r, .05)) if n else None
    return {
        "observations": n, "volatility": vol * np.sqrt(252) if n > 1 else None,
        "sharpe": mean / vol * np.sqrt(252) if vol > 1e-12 else None,
        "sortino": mean / downside * np.sqrt(252) if downside > 1e-12 else None,
        "beta": beta,
        "correlation": float(np.corrcoef(r, b)[0, 1]) if vol > 1e-12 and bvol > 1e-12 else None,
        "alpha_annual": (mean - beta * float(b.mean())) * 252 if beta is not None else None,
        "tracking_error": tracking * np.sqrt(252) if n > 1 else None,
        "information_ratio": float(active.mean()) / tracking * np.sqrt(252) if tracking > 1e-12 else None,
        "var95": max(0.0, -cutoff) if cutoff is not None else None,
        "es95": max(0.0, -float(r[r <= cutoff].mean())) if cutoff is not None else None,
        "positive_days": int(sum(r > 1e-12)), "negative_days": int(sum(r < -1e-12)),
        "flat_days": int(sum(np.abs(r) <= 1e-12)),
    }


def movement(before: int, after: int) -> str:
    if before == after:
        return "Sem negociação líquida"
    if not before:
        return "Abertura long" if after > 0 else "Abertura short"
    if not after:
        return "Encerramento long" if before > 0 else "Encerramento short"
    if before * after < 0:
        return "Inversão para long" if after > 0 else "Inversão para short"
    return ("Aumento " if abs(after) > abs(before) else "Redução ") + ("long" if after > 0 else "short")


def analyze(data: dict) -> dict:
    records, dates = data["records"], data["dates"]
    nav, bnav = data["nav"], data["benchmark_nav"]
    capital = data["capital"]
    if not records or not (len(records) == len(dates) == len(nav) == len(bnav)):
        raise ValueError("Séries e registros devem ter o mesmo tamanho não nulo.")
    if dates != sorted(set(dates)) or [r["session"] for r in records] != dates:
        raise ValueError("As datas devem ser únicas, ordenadas e alinhadas aos registros.")
    if capital <= 0 or not np.isfinite(capital) or any(not np.isfinite(v) or v <= 0 for v in [*nav, *bnav]):
        raise ValueError("Capital, NAV e benchmark devem ser positivos e finitos.")
    for i, record in enumerate(records):
        if abs(record["nav"] - nav[i]) > .01:
            raise ValueError(f"NAV divergente em {dates[i]}.")
        for ticker, quantity in record["positions"].items():
            mark = record["marks"].get(ticker)
            if quantity and (mark is None or not np.isfinite(mark) or mark <= 0):
                raise ValueError(f"Marcação ausente ou inválida: {ticker} em {dates[i]}.")
    returns = [nav[i] / nav[i - 1] - 1 for i in range(1, len(nav))]
    benchmark_returns = [bnav[i] / bnav[i - 1] - 1 for i in range(1, len(bnav))]
    days, all_trades = [], []
    asset_totals = defaultdict(lambda: {"pnl": 0.0, "buys": 0.0, "sells": 0.0, "fills": 0, "held_days": 0, "max_weight": 0.0, "sum_weight": 0.0})
    peak, benchmark_peak, underwater, max_underwater = nav[0], bnav[0], 0, 0
    for i, record in enumerate(records):
        previous = records[i - 1] if i else record
        executed, decision = record.get("executed") or {}, record.get("decision") or {}
        fills = executed.get("fills") or []
        traded, quantities = defaultdict(float), defaultdict(int)
        running = dict(previous["positions"]) if i else {}
        buys = sells = 0.0
        for fill in fills:
            t, side, q, price = (fill[k] for k in ("ticker", "side", "quantity", "price"))
            if side not in ("buy", "sell") or q <= 0 or price <= 0 or not np.isfinite(q * price):
                raise ValueError(f"Execução inválida em {dates[i]}: {t}")
            signed = q if side == "buy" else -q
            before = running.get(t, 0)
            running[t] = before + signed
            trade = {"date": dates[i], "ticker": t, "side": side, "quantity": q, "price": price, "notional": q * price, "before": before, "after": running[t], "movement": movement(before, running[t])}
            all_trades.append(trade)
            traded[t] += signed * price
            quantities[t] += signed
            if side == "buy":
                buys += q * price
            else:
                sells += q * price
            asset_totals[t]["buys" if side == "buy" else "sells"] += q * price
            asset_totals[t]["fills"] += 1
        positions, quantity_errors = [], []
        for t in sorted(set(record["positions"]) | set(previous["positions"]) | set(quantities)):
            before, after = previous["positions"].get(t, 0), record["positions"].get(t, 0)
            old_value = before * previous["marks"].get(t, 0)
            mark = record["marks"].get(t, executed.get("marks", {}).get(t))
            value = after * mark if after else 0.0
            weight, old_weight = value / nav[i], old_value / previous["nav"]
            pnl = value - old_value - traded[t] if i else 0.0
            if i and after - before != quantities[t]:
                quantity_errors.append(t)
            positions.append({"ticker": t, "before": before, "after": after, "delta": after - before if i else 0, "mark": mark, "previous_value": old_value, "value": value, "value_change": value - old_value if i else 0.0, "previous_weight": old_weight, "weight": weight, "weight_change": weight - old_weight if i else 0.0, "traded": traded[t], "pnl": pnl, "contribution": pnl / previous["nav"] if i else 0.0, "movement": movement(before, after), "target": decision.get("final_weights", {}).get(t)})
            a = asset_totals[t]
            a["pnl"] += pnl
            a["held_days"] += int(after != 0)
            a["max_weight"] = max(a["max_weight"], abs(weight))
            a["sum_weight"] += abs(weight)
        weights = sorted([abs(p["weight"]) for p in positions if p["after"]], reverse=True)
        gross = sum(weights)
        fractions = [w / gross for w in weights] if gross else []
        hhi = sum(w ** 2 for w in fractions) if fractions else None
        long = sum(p["weight"] for p in positions if p["weight"] > 0)
        short = -sum(p["weight"] for p in positions if p["weight"] < 0)
        peak, benchmark_peak = max(peak, nav[i]), max(benchmark_peak, bnav[i])
        underwater = underwater + 1 if nav[i] < peak - 1e-8 else 0
        max_underwater = max(max_underwater, underwater)
        pnl = nav[i] - nav[i - 1] if i else 0.0
        signals = {}
        for strategy in decision.get("strategies", []):
            for signal in strategy.get("signals", []):
                signals.setdefault(signal["ticker"], {})[signal["model_name"]] = {"value": signal["value"], "reasoning": signal.get("reasoning") or "Sem justificativa registrada."}
        days.append({"date": dates[i], "nav": nav[i], "benchmark_nav": bnav[i], "return": returns[i - 1] if i else None, "benchmark_return": benchmark_returns[i - 1] if i else None, "cumulative": nav[i] / capital - 1, "benchmark_cumulative": bnav[i] / bnav[0] - 1, "drawdown": nav[i] / peak - 1, "benchmark_drawdown": bnav[i] / benchmark_peak - 1, "cash": record["cash"], "cash_weight": record["cash"] / nav[i], "long": long, "short": short, "gross": gross, "net": long - short, "count": len(weights), "hhi": hhi, "effective_n": 1 / hhi if hhi else None, "top1": fractions[0] if fractions else None, "top5": sum(fractions[:5]) if fractions else None, "max_weight": weights[0] if weights else 0.0, "buys": buys, "sells": sells, "volume": buys + sells, "turnover": (buys + sells) / previous["nav"], "fills": len(fills), "pnl": pnl, "positions": positions, "targets": decision.get("final_weights", {}), "signals": signals, "clamps": decision.get("clamps", []), "skipped": decision.get("skipped", []), "rolling": risk_metrics(returns[i - 20:i], benchmark_returns[i - 20:i]) if i >= 20 else None, "nav_residual": nav[i] - record["cash"] - sum(p["value"] for p in positions), "cash_residual": record["cash"] - previous["cash"] + buys - sells if i else 0.0, "pnl_residual": pnl - sum(p["pnl"] for p in positions), "quantity_errors": quantity_errors})
    assets = []
    for t, a in asset_totals.items():
        aligned_r, aligned_b = [], []
        for i in range(1, len(records)):
            prices = []
            for r in (records[i - 1], records[i]):
                prices.append(r["marks"].get(t, (r.get("decision") or {}).get("marks", {}).get(t, (r.get("executed") or {}).get("marks", {}).get(t))))
            if all(p is not None and p > 0 for p in prices):
                aligned_r.append(prices[1] / prices[0] - 1)
                aligned_b.append(benchmark_returns[i - 1])
        last = next((p for p in days[-1]["positions"] if p["ticker"] == t), {})
        assets.append({"ticker": t, **a, "contribution": a["pnl"] / capital, "average_weight": a["sum_weight"] / len(days), "final_weight": last.get("weight", 0), "final_quantity": last.get("after", 0), "risk": risk_metrics(aligned_r, aligned_b)})
    months = []
    for month in dict.fromkeys(d[:7] for d in dates):
        indices = [i for i, d in enumerate(dates) if d.startswith(month)]
        end, before = indices[-1], max(0, indices[0] - 1)
        months.append({"month": month, "return": nav[end] / nav[before] - 1, "benchmark": bnav[end] / bnav[before] - 1, "pnl": nav[end] - nav[before], "observations": sum(i > 0 for i in indices)})
    total = nav[-1] / capital - 1
    benchmark_total = bnav[-1] / bnav[0] - 1
    calendar_days = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
    annual = (1 + total) ** (365.25 / calendar_days) - 1 if calendar_days else None
    max_dd = min(d["drawdown"] for d in days)
    trough = min(range(len(days)), key=lambda i: days[i]["drawdown"])
    peak_index = max(range(trough + 1), key=lambda i: nav[i])
    recovered = next((dates[i] for i in range(trough + 1, len(days)) if nav[i] >= nav[peak_index]), None) if max_dd < -1e-12 else None
    best = max(days[1:], key=lambda d: d["return"]) if returns else None
    worst = min(days[1:], key=lambda d: d["return"]) if returns else None
    metrics = risk_metrics(returns, benchmark_returns)
    summary = {**metrics, "total_return": total, "benchmark_return": benchmark_total, "excess": total - benchmark_total, "relative_return": (1 + total) / (1 + benchmark_total) - 1, "profit": nav[-1] - capital, "final_nav": nav[-1], "annualized": annual, "max_drawdown": max_dd, "calmar": annual / abs(max_dd) if annual is not None and max_dd < -1e-12 else None, "drawdown_peak": dates[peak_index], "drawdown_trough": dates[trough], "drawdown_recovery": recovered, "max_underwater": max_underwater, "fills": len(all_trades), "orders": sum(len((r.get("executed") or {}).get("orders", [])) for r in records), "cycles": sum(r.get("executed") is not None for r in records), "buys": sum(d["buys"] for d in days), "sells": sum(d["sells"] for d in days), "volume": sum(d["volume"] for d in days), "turnover": sum(d["turnover"] for d in days), "average_gross": float(np.mean([d["gross"] for d in days])), "average_net": float(np.mean([d["net"] for d in days])), "best_date": best["date"] if best else None, "best_return": best["return"] if best else None, "best_pnl": best["pnl"] if best else None, "worst_date": worst["date"] if worst else None, "worst_return": worst["return"] if worst else None, "return_without_best": (1 + total) / (1 + best["return"]) - 1 if best else None, "profit_best_share": best["pnl"] / (nav[-1] - capital) if best and abs(nav[-1] - capital) > .01 else None}
    warnings = ["Backtest exploratório, não um histórico de operações reais. A amostra curta torna beta, alfa, Sharpe, VaR e anualizações instáveis.", "Sharpe, Sortino e alfa usam taxa livre de risco e retorno mínimo iguais a zero: não representam excesso sobre o CDI.", "Sem custos de transação, slippage, impostos, aluguel de ações ou juros sobre caixa. Caixa em carteira long/short não é sinônimo de capital disponível para saque.", "Ajustes de preços por desdobramentos, dividendos e JCP precisam de validação. O simulador não credita eventos corporativos separadamente.", "Disponibilidade histórica e revisões dos fundamentos não são comprovadamente point-in-time; o universo pode conter viés de sobrevivência e seleção.", "Setores, liquidez, capacidade, margem e custos de aluguel não estão disponíveis de forma estruturada para esta análise; não foram inferidos."]
    bad = [d["date"] for d in days if max(abs(d[k]) for k in ("nav_residual", "cash_residual", "pnl_residual")) > .01 or d["quantity_errors"]]
    if bad:
        warnings.insert(0, f"Reconciliação: divergências em {len(bad)} sessões ({', '.join(bad)}). Atribuição e movimentos exigem revisão.")
    if abs(nav[0] - capital) > .01:
        warnings.insert(0, "O NAV inicial difere do capital. A atribuição começa no primeiro fechamento, enquanto o retorno total usa o capital informado.")
    if best and abs(best["return"]) > .05:
        warnings.insert(0, f"Movimento expressivo em {best['date']}: {best['return']:.2%} no fundo. Confira preços e eventos corporativos na fonte antes de interpretar como desempenho recorrente.")
    return {"fund": data["fund"], "start": dates[0], "end": dates[-1], "capital": capital, "benchmark": data["benchmark"], "universe": data.get("universe", []), "source": data.get("data_source", "Não informada"), "currency": data.get("currency", "BRL"), "reliability": data.get("historical_reliability", "Não informada"), "source_limitations": data.get("limitations", []), "source_metrics": data.get("metrics", {}), "summary": summary, "days": days, "trades": all_trades, "assets": sorted(assets, key=lambda a: -a["pnl"]), "months": months, "warnings": warnings, "reconciliation_ok": not bad}


def render(data: dict, source_name: str = "") -> str:
    report = analyze(data)
    report["source_file"] = source_name
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (Path(__file__).resolve().parents[1] / "templates" / "portfolio_report.html").read_text(encoding="utf-8")
    return template.replace("__TITLE__", html.escape(data["fund"])).replace("__REPORT_DATA__", payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera um painel HTML offline de análise de portfólio.")
    parser.add_argument("result", type=Path)
    parser.add_argument("-o", "--out", type=Path)
    args = parser.parse_args()
    data = json.loads(args.result.read_text(encoding="utf-8"))
    out = args.out or args.result.with_name(args.result.stem + "-portfolio.html")
    page = render(data, args.result.name)
    with out.open("x", encoding="utf-8") as output:
        output.write(page)
    print(out)
    report = analyze(data)
    print(json.dumps({"summary": report["summary"], "reconciliation_ok": report["reconciliation_ok"], "source": report["source"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
