import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from hedge_fund.backtesting.portfolio_report import analyze, render, risk_metrics


def sample():
    dates = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"]
    books = [({}, {}, 1000), ({"AAA3": 10, "BBB4": -5}, {"AAA3": 20, "BBB4": 10}, 850), ({"AAA3": 6, "BBB4": -2}, {"AAA3": 22, "BBB4": 8}, 914), ({}, {}, 1034)]
    fills = [[], [("AAA3", "buy", 10, 20), ("BBB4", "sell", 5, 10)], [("AAA3", "sell", 4, 22), ("BBB4", "buy", 3, 8)], [("AAA3", "sell", 6, 23), ("BBB4", "buy", 2, 9)]]
    records = []
    for day, (positions, marks, cash), trades in zip(dates, books, fills):
        executed = {"fills": [dict(ticker=t, side=s, quantity=q, price=p) for t, s, q, p in trades], "marks": {t: p for t, _, _, p in trades}}
        records.append(dict(session=day, positions=positions, marks=marks, cash=cash, nav=cash + sum(q * marks[t] for t, q in positions.items()), executed=executed if trades else None, decision=None))
    return dict(fund="Teste", start=dates[0], end=dates[-1], dates=dates, nav=[r["nav"] for r in records], benchmark_nav=[1000, 1010, 1020, 1030], capital=1000, benchmark="BOVA11", universe=["AAA3", "BBB4"], records=records)


def test_positions_fills_and_pnl_reconcile_including_short_cover_and_exit():
    result = analyze(sample())
    day = result["days"][2]
    long, short = sorted(day["positions"], key=lambda p: p["ticker"])
    assert (long["before"], long["after"], long["delta"]) == (10, 6, -4)
    assert long["pnl"] == pytest.approx(20)
    assert long["value_change"] == pytest.approx(-68)
    assert (short["before"], short["after"], short["delta"]) == (-5, -2, 3)
    assert short["movement"] == "Redução short"
    assert short["pnl"] == pytest.approx(10)
    assert day["pnl"] == pytest.approx(30)
    assert day["cash_residual"] == pytest.approx(0)
    assert day["pnl_residual"] == pytest.approx(0)
    assert result["days"][-1]["gross"] == 0
    assert {p["movement"] for p in result["days"][-1]["positions"]} == {"Encerramento long", "Encerramento short"}
    assert sum(a["pnl"] for a in result["assets"]) == pytest.approx(34)
    assert result["summary"]["total_return"] == pytest.approx(.034)
    assert result["summary"]["fills"] == 6


def test_concentration_uses_absolute_exposure_not_signed_weights():
    day = analyze(sample())["days"][1]
    assert day["gross"] == pytest.approx(.25)
    assert day["net"] == pytest.approx(.15)
    assert day["hhi"] == pytest.approx(.8 ** 2 + .2 ** 2)
    assert day["effective_n"] == pytest.approx(1 / .68)
    assert day["top1"] == pytest.approx(.8)
    assert day["top5"] == pytest.approx(1)


def test_beta_uses_aligned_daily_returns_and_no_initial_zero():
    bench = [.01, -.02, .03, -.01]
    returns = [2 * r + .001 for r in bench]
    metrics = risk_metrics(returns, bench)
    assert metrics["beta"] == pytest.approx(2)
    assert metrics["correlation"] == pytest.approx(1)
    assert metrics["alpha_annual"] == pytest.approx(.252)
    assert metrics["observations"] == 4


def test_undefined_risk_and_no_positions_are_null_not_infinity():
    metrics = risk_metrics([0, 0], [0, 0])
    assert metrics["beta"] is None
    assert metrics["sharpe"] is None
    assert metrics["sortino"] is None
    result = analyze(sample())
    assert result["days"][0]["hhi"] is None
    assert result["days"][0]["return"] is None
    assert result["summary"]["observations"] == 3
    json.dumps(result, allow_nan=False)


def test_missing_marks_and_misaligned_dates_fail_loudly():
    data = sample()
    del data["records"][1]["marks"]["AAA3"]
    with pytest.raises(ValueError, match="AAA3"):
        analyze(data)
    data = sample()
    data["dates"][1] = "2026-08-07"
    with pytest.raises(ValueError, match="datas"):
        analyze(data)


def test_inconsistent_fills_are_flagged_and_not_replaced_with_orders():
    data = sample()
    data["records"][1]["executed"]["fills"] = []
    data["records"][1]["executed"]["orders"] = [{"ticker": "AAA3", "side": "buy", "quantity": 10, "price": 20}]
    result = analyze(data)
    assert result["days"][1]["quantity_errors"] == ["AAA3", "BBB4"]
    assert result["summary"]["fills"] == 4
    assert any("Reconciliação" in w for w in result["warnings"])


def test_monthly_returns_compound_and_attribution_adds_to_total_return():
    result = analyze(sample())
    assert result["months"][0]["return"] == pytest.approx(.034)
    assert sum(a["contribution"] for a in result["assets"]) == pytest.approx(.034)
    assert result["summary"]["return_without_best"] == pytest.approx(1034 / 1030 - 1)


@pytest.mark.parametrize("before,after,expected", [(0, -5, "Abertura short"), (-5, -8, "Aumento short"), (-5, -2, "Redução short"), (-5, 3, "Inversão para long"), (5, -3, "Inversão para short"), (5, 8, "Aumento long")])
def test_movement_distinguishes_short_exposure_from_trade_side(before, after, expected):
    from hedge_fund.backtesting.portfolio_report import movement

    assert movement(before, after) == expected


def test_monthly_returns_carry_previous_month_close():
    data = sample()
    data["dates"] = ["2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02"]
    for record, day in zip(data["records"], data["dates"]):
        record["session"] = day
    result = analyze(data)
    assert len(result["months"]) == 2
    assert (1 + result["months"][0]["return"]) * (1 + result["months"][1]["return"]) - 1 == pytest.approx(result["summary"]["total_return"])
    assert sum(m["pnl"] for m in result["months"]) == pytest.approx(result["summary"]["profit"])


def test_historical_tail_risk_uses_linear_percentile_and_tail_mean():
    result = risk_metrics([-.04, -.02, .01, .03], [-.01, .02, .01, .03])
    assert result["var95"] == pytest.approx(.037)
    assert result["es95"] == pytest.approx(.04)


def test_html_is_offline_and_embedded_data_cannot_break_out_of_script():
    data = copy.deepcopy(sample())
    data["fund"] = "</script><script>alert('x')</script>"
    page = render(data)
    assert '<html lang="pt-BR">' in page
    assert '<script src=' not in page
    assert "</script><script>alert" not in page
    assert 'id="report-data"' in page
    assert "Composição diária" in page
    assert "Metodologia" in page


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required to execute the template bootstrap")
def test_opening_raw_template_redirects_to_populated_report_without_parsing_placeholder():
    template = (Path(__file__).resolve().parents[1] / "templates" / "portfolio_report.html").read_text(encoding="utf-8")
    script = template.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = """
const vm = require('node:vm');
let target;
const nodes = {};
const document = {
  getElementById: () => ({textContent: '__REPORT_DATA__'}),
  querySelector: selector => nodes[selector] ??= {},
};
const window = {location: {
  href: 'file:///repo/hedge_fund/templates/portfolio_report.html',
  replace: url => target = String(url),
}};
vm.runInNewContext(SCRIPT, {document, window, URL});
require('node:assert/strict').equal(target, 'file:///repo/outputs/portfolio/index.html');
require('node:assert/strict').match(nodes.main.innerHTML, /Abrir relatório preenchido/);
""".replace("SCRIPT", json.dumps(script))
    result = subprocess.run(["node", "-"], input=harness, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
