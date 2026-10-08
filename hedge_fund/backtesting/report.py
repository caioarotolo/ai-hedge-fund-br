"""Render a backtest result JSON as a single self-contained HTML report.

Usage::

    python -m hedge_fund.backtesting.report ~/.hedge-fund/research/<file>.json [-o out.html]

No dependencies: charts are inline SVG, interactivity is a few lines of
vanilla JS, reasoning lives in <details> so a long run stays browsable.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

_H = html.escape

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _pct(x: float, digits: int = 1, signed: bool = True) -> str:
    s = f"{x:+.{digits}%}" if signed else f"{x:.{digits}%}"
    return s.replace(".", ",")


def _num(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def _brl(x: float) -> str:
    return f"R${_num(x)}"


def _w(x: float) -> str:  # weight as %
    return _pct(x, digits=2)


# ---------------------------------------------------------------------------
# SVG charts
# ---------------------------------------------------------------------------

def _line_chart(
    series: list[tuple[str, list[float], str]],
    dates: list[str],
    width: int = 1100,
    height: int = 320,
    y_fmt=lambda v: f"{v:.0f}",
) -> str:
    """Multi-line chart. series = [(name, values, color)]."""
    if not dates:
        return ""
    n = len(dates)
    lo = min(min(vals) for _, vals, _ in series)
    hi = max(max(vals) for _, vals, _ in series)
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad
    ml, mr, mt, mb = 64, 16, 12, 30
    pw, ph = width - ml - mr, height - mt - mb

    def px(i: int) -> float:
        return ml + pw * i / max(n - 1, 1)

    def py(v: float) -> float:
        return mt + ph * (1 - (v - lo) / (hi - lo))

    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" preserveAspectRatio="none">']
    # horizontal grid + y labels (5 ticks)
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = py(v)
        parts.append(
            f'<line x1="{ml}" y1="{y:.1f}" x2="{width - mr}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{ml - 8}" y="{y + 4:.1f}" class="ylabel" text-anchor="end">{_H(y_fmt(v))}</text>'
        )
    # x labels: first, quarter, mid, three-quarter, last
    for i in sorted({0, n // 4, n // 2, (3 * n) // 4, n - 1}):
        parts.append(
            f'<text x="{px(i):.1f}" y="{height - 8}" class="xlabel" text-anchor="middle">{_H(dates[i][5:])}</text>'
        )
    for name, vals, color in series:
        pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(vals))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" class="series"/>')
        for i, v in enumerate(vals):
            parts.append(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="0" data-i="{i}" class="{_H(name)}-dot"/>')
    parts.append("</svg>")
    return "".join(parts)


def _drawdown_series(nav: list[float]) -> list[float]:
    peak, out = None, []
    for v in nav:
        peak = v if peak is None else max(peak, v)
        out.append(v / peak - 1)
    return out


def _drawdown_chart(dd: list[float], dates: list[str], width: int = 1100, height: int = 110) -> str:
    if not dd:
        return ""
    n = len(dates)
    lo = min(dd) * 1.1 or -0.01
    ml, mr, mt, mb = 64, 16, 6, 24
    pw, ph = width - ml - mr, height - mt - mb

    def px(i: int) -> float:
        return ml + pw * i / max(n - 1, 1)

    def py(v: float) -> float:
        return mt + ph * (v / lo)  # lo is negative; v/lo in [0,1]

    pts = f"{ml},{mt} " + " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(dd))
    pts += f" {px(n - 1):.1f},{mt}"
    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" preserveAspectRatio="none">']
    parts.append(f'<polygon points="{pts}" class="ddfill"/>')
    for i in sorted({0, n // 4, n // 2, (3 * n) // 4, n - 1}):
        parts.append(
            f'<text x="{px(i):.1f}" y="{height - 6}" class="xlabel" text-anchor="middle">{_H(dates[i][5:])}</text>'
        )
    parts.append(f'<text x="{ml - 8}" y="{py(lo / 1.1) + 4:.1f}" class="ylabel" text-anchor="end">{_pct(min(dd))}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Book: positions after each session's close
# ---------------------------------------------------------------------------

def _book_stats(record: dict) -> dict[str, float]:
    nav = record["nav"]
    long_w = short_w = 0.0
    n = 0
    for t, shares in (record.get("positions") or {}).items():
        if shares == 0 or t not in (record.get("marks") or {}):
            continue
        w = shares * record["marks"][t] / nav
        long_w += w if w > 0 else 0.0
        short_w += w if w < 0 else 0.0
        n += 1
    return {"long": long_w, "short": short_w, "gross": long_w - short_w,
            "net": long_w + short_w, "cash": record["cash"] / nav, "n": n}


def _exposure_series(records: list[dict]) -> tuple[list[float], list[float]]:
    gross, net = [], []
    for r in records:
        s = _book_stats(r)
        gross.append(s["gross"])
        net.append(s["net"])
    return gross, net


def _book_html(record: dict) -> str:
    nav, marks = record["nav"], record.get("marks") or {}
    book = []
    for t, shares in (record.get("positions") or {}).items():
        if shares == 0 or t not in marks:
            continue
        val = shares * marks[t]
        book.append((t, shares, marks[t], val, val / nav))
    if not book:
        return "<p class='muted'>Book is all cash.</p>"
    book.sort(key=lambda b: -abs(b[4]))
    rows = [
        f"<tr><td class='tk'>{_H(t)}</td><td class='num {'long' if shares > 0 else 'short'}'>{shares:+d}</td>"
        f"<td class='num'>{_num(mark)}</td><td class='num'>{_brl(val)}</td>"
        f"<td class='num {'long' if shares > 0 else 'short'}'>{_w(w)}</td></tr>"
        for t, shares, mark, val, w in book
    ]
    s = _book_stats(record)
    stats = (
        f"<div class='bookstats'>"
        f"<span><b>{s['n']}</b> positions</span>"
        f"<span>long <b class='long'>{_pct(s['long'])}</b></span>"
        f"<span>short <b class='short'>{_pct(s['short'])}</b></span>"
        f"<span>gross <b>{_pct(s['gross'])}</b></span>"
        f"<span>net <b>{_pct(s['net'])}</b></span>"
        f"<span>cash <b>{_pct(s['cash'])}</b></span>"
        f"</div>"
    )
    head = "<tr><th>ticker</th><th>shares</th><th>mark</th><th>value</th><th>weight</th></tr>"
    return f"<h4>Book at close</h4>{stats}<table class='tbl'>{head}{''.join(rows)}</table>"


# ---------------------------------------------------------------------------
# Heatmap: tickers x sessions, cell = decided target weight
# ---------------------------------------------------------------------------

def _heatmap(records: list[dict], universe: list[str]) -> str:
    sessions = [r["session"] for r in records]
    weights: dict[str, dict[str, float]] = {}
    for r in records:
        src = r.get("decision") or r.get("executed")
        w = (src or {}).get("final_weights") or {}
        for t, v in w.items():
            weights.setdefault(t, {})[r["session"]] = v
    order = sorted(universe, key=lambda t: -sum(abs(v) for v in weights.get(t, {}).values()))
    wmax = max((abs(v) for m in weights.values() for v in m.values()), default=0.0) or 1.0

    out = ['<div class="heat"><div class="heat-row heat-head"><span class="heat-t"></span>']
    step = max(1, len(sessions) // 12)
    for i, s in enumerate(sessions):
        label = s[5:] if i % step == 0 else ""
        out.append(f'<span class="heat-c"><i>{_H(label)}</i></span>')
    out.append("</div>")
    for t in order:
        row = weights.get(t, {})
        out.append(f'<div class="heat-row"><span class="heat-t">{_H(t)}</span>')
        for s in sessions:
            v = row.get(s)
            if v is None:
                out.append('<span class="heat-c"></span>')
            else:
                a = min(abs(v) / wmax, 1.0)
                color = f"rgba(63,185,80,{a:.2f})" if v > 0 else (f"rgba(248,81,73,{a:.2f})" if v < 0 else "rgba(139,148,158,.12)")
                out.append(
                    f'<span class="heat-c" style="background:{color}" '
                    f'title="{_H(t)} · {_H(s)} · {_w(v)}"></span>'
                )
        out.append("</div>")
    out.append("</div>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _orders_table(executed: dict) -> str:
    rows = []
    for o in executed.get("orders", []):
        side = "buy" if o["side"] == "buy" else "sell"
        val = o["quantity"] * o["price"]
        rows.append(
            f"<tr><td>{_H(o['ticker'])}</td><td class='{side}'>{side}</td>"
            f"<td class='num'>{o['quantity']}</td><td class='num'>{_num(o['price'])}</td>"
            f"<td class='num'>{_brl(val)}</td></tr>"
        )
    if not rows:
        return ""
    head = "<tr><th>ticker</th><th>side</th><th>qty</th><th>price</th><th>value</th></tr>"
    return f"<h4>Executed orders ({len(rows)})</h4><table class='tbl'>{head}{''.join(rows)}</table>"


def _weights_table(decision: dict) -> str:
    fw = decision.get("final_weights") or {}
    items = [(t, w) for t, w in fw.items() if abs(w) > 1e-9]
    if not items:
        return ""
    items.sort(key=lambda kv: -abs(kv[1]))
    wmax = max(abs(w) for _, w in items) or 1.0
    rows = []
    for t, w in items:
        pct = abs(w) / wmax * 100
        cls = "long" if w > 0 else "short"
        rows.append(
            f"<tr><td class='tk'>{_H(t)}</td><td class='num {cls}'>{_w(w)}</td>"
            f"<td class='bar'><i class='{cls}' style='width:{pct:.0f}%'></i></td></tr>"
        )
    return (
        f"<h4>Target weights (post-risk)</h4><table class='tbl wtbl'>"
        f"<tr><th>ticker</th><th>weight</th><th></th></tr>{''.join(rows)}</table>"
    )


def _clamps_html(decision: dict) -> str:
    clamps = decision.get("clamps") or []
    if not clamps:
        return ""
    lis = []
    for c in clamps:
        name = c["limit"].replace("_", " ")
        who = f" · {_H(c['ticker'])}" if c.get("ticker") else ""
        lis.append(f"<li><b>{_H(name)}</b>{who}: {_pct(c['before'],2)} → {_pct(c['after'],2)}</li>")
    return f"<div class='clamps'><h4>Risk clamps ({len(clamps)})</h4><ul>{''.join(lis)}</ul></div>"


def _signals_html(decision: dict) -> str:
    """Per-ticker cards: deduped (model, ticker) signals + reasoning."""
    # dedupe cache-hit duplicates: same model+ticker repeats across strategies
    by_ticker: dict[str, dict[str, dict]] = {}
    for strat in decision.get("strategies", []):
        for sig in strat.get("signals", []):
            slot = by_ticker.setdefault(sig["ticker"], {}).setdefault(
                sig["model_name"],
                {"value": sig["value"], "reasoning": sig.get("reasoning") or "", "in": []},
            )
            if strat["name"] not in slot["in"]:
                slot["in"].append(strat["name"])
    if not by_ticker:
        return ""
    fw = decision.get("final_weights") or {}

    def key(t: str) -> tuple:
        return (abs(fw.get(t, 0.0)), max((abs(s["value"]) for s in by_ticker[t].values()), default=0.0))

    cards = []
    for t in sorted(by_ticker, key=key, reverse=True):
        w = fw.get(t, 0.0)
        wcls = "long" if w > 0 else ("short" if w < 0 else "flat")
        chips = []
        for model, s in sorted(by_ticker[t].items()):
            v = s["value"]
            cv = "pos" if v > 0.05 else ("neg" if v < -0.05 else "neu")
            chips.append(
                f"<details class='sig'><summary><b>{_H(model)}</b>"
                f"<i class='conv {cv}'>{v:+.2f}</i></summary>"
                f"<p class='reason'>{_H(s['reasoning'])}</p></details>"
            )
        cards.append(
            f"<div class='tkcard'><div class='tkhead'><b>{_H(t)}</b>"
            f"<i class='{wcls}'>{_w(w) if abs(w)>1e-9 else '—'}</i></div>{''.join(chips)}</div>"
        )
    return f"<h4>Agent views ({len(by_ticker)} tickers)</h4><div class='tkgrid'>{''.join(cards)}</div>"


def _sessions_html(records: list[dict]) -> str:
    out = []
    prev_nav = None
    day_html: list[tuple[str, str]] = []
    for r in records:
        nav = r["nav"]
        delta = nav / prev_nav - 1 if prev_nav else 0.0
        prev_nav = nav
        executed, decision = r.get("executed"), r.get("decision")
        badges = []
        if executed:
            badges.append(f"<i class='chip exec'>executed {len(executed['orders'])} orders</i>")
        if decision:
            badges.append("<i class='chip dec'>decision</i>")
        if not executed and not decision:
            badges.append("<i class='chip muted'>marked the book</i>")
        skipped = (decision or {}).get("skipped") or []
        if skipped:
            badges.append(f"<i class='chip skip'>skipped {len(skipped)}</i>")
        dcls = "up" if delta >= 0 else "dn"
        summary = (
            f"<summary><span class='sdate'>{_H(r['session'])}</span>"
            f"<span class='snav {_H(dcls)}'>{_brl(nav)} <small>{_pct(delta,2) if prev_nav else ''}</small></span>"
            f"<span class='sbadges'>{''.join(badges)}</span></summary>"
        )
        body = ["<div class='sbody'>"]
        body.append(_book_html(r))
        if executed:
            body.append(_orders_table(executed))
        if decision:
            body.append(_clamps_html(decision))
            body.append(_weights_table(decision))
            body.append(_signals_html(decision))
        elif executed is None and not decision:
            body.append("<p class='muted'>Book marked to market; no rebalance due.</p>")
        body.append("</div>")
        day_html.append((r["session"], f"<details class='sess'>{summary}{''.join(body)}</details>"))
    for _, h in reversed(day_html):  # latest session first
        out.append(h)
    return "".join(out)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

_CSS = """
:root{--bg:#0d1117;--card:#161b22;--line:#30363d;--tx:#e6edf3;--mut:#8b949e;
--grn:#3fb950;--red:#f85149;--blu:#58a6ff;--ylw:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1160px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:15px;color:var(--mut);margin:34px 0 10px;
text-transform:uppercase;letter-spacing:.08em}h4{margin:14px 0 6px;font-size:13px;color:var(--mut)}
.sub{color:var(--mut);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:20px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .l{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
.card .v{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.card .v small{font-size:12px;color:var(--mut);font-weight:400}
.pos{color:var(--grn)}.neg{color:var(--red)}.long{color:var(--grn)}.short{color:var(--red)}
.flat,.muted{color:var(--mut)}
.chart{width:100%;height:auto;display:block;background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:8px}
.grid{stroke:var(--line);stroke-width:.5}.ylabel,.xlabel{fill:var(--mut);font-size:11px}
.series{vector-effect:non-scaling-stroke}.ddfill{fill:rgba(248,81,73,.35);stroke:#f85149;stroke-width:1}
.tip{position:fixed;pointer-events:none;background:#010409;border:1px solid var(--line);
border-radius:8px;padding:8px 10px;font-size:12px;display:none;z-index:9;white-space:pre}
.heat{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
.heat-row{display:flex;gap:2px;align-items:center;min-width:max-content}
.heat-t{width:58px;font-size:11px;color:var(--mut);flex:none}
.heat-c{width:12px;height:14px;border-radius:2px;flex:none;position:relative}
.heat-head .heat-c{height:16px}.heat-head i{font-style:normal;font-size:9px;color:var(--mut);
position:absolute;top:0;left:0;transform:rotate(-40deg);transform-origin:0 0;white-space:nowrap}
.sess{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0}
.sess summary{display:flex;gap:14px;align-items:center;padding:10px 14px;cursor:pointer;list-style:none}
.sess summary::-webkit-details-marker{display:none}
.sess summary::before{content:"▸";color:var(--mut)}
.sess[open] summary::before{content:"▾"}
.sdate{font-weight:600;font-variant-numeric:tabular-nums}
.snav{font-variant-numeric:tabular-nums}.snav.up{color:var(--grn)}.snav.dn{color:var(--red)}
.snav small{color:var(--mut)}
.sbadges{margin-left:auto;display:flex;gap:6px}
.chip{font-style:normal;font-size:11px;border:1px solid var(--line);border-radius:99px;
padding:1px 8px;color:var(--mut)}
.chip.exec{color:var(--blu);border-color:var(--blu)}.chip.dec{color:var(--grn);border-color:var(--grn)}
.chip.skip{color:var(--ylw);border-color:var(--ylw)}
.bookstats{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:var(--mut);margin:6px 0 8px}
.bookstats b{color:var(--tx);font-variant-numeric:tabular-nums}
.sbody{padding:0 14px 14px;border-top:1px solid var(--line)}
.tbl{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
.tbl th{text-align:left;font-size:11px;color:var(--mut);font-weight:600;padding:4px 8px;
border-bottom:1px solid var(--line)}
.tbl td{padding:4px 8px;border-bottom:1px solid #21262d;font-size:13px}
.tbl .num{text-align:right}.tbl .buy{color:var(--grn)}.tbl .sell{color:var(--red)}
.tbl .tk{font-weight:600}
.wtbl .bar{width:40%}.wtbl .bar i{display:block;height:8px;border-radius:3px}
.wtbl .bar i.long{background:var(--grn)}.wtbl .bar i.short{background:var(--red)}
.clamps{background:rgba(248,81,73,.08);border:1px solid rgba(248,81,73,.4);border-radius:8px;
padding:8px 12px;margin:10px 0}
.clamps ul{margin:4px 0;padding-left:18px}
.tkgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:8px;margin-top:6px}
.tkcard{border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.tkhead{display:flex;justify-content:space-between;font-variant-numeric:tabular-nums;margin-bottom:4px}
.sig{margin:3px 0}.sig summary{cursor:pointer;list-style:none;display:flex;gap:8px;align-items:center;
font-size:12.5px;color:var(--tx)}
.sig summary::before{content:"+";color:var(--mut)}
.sig[open] summary::before{content:"−"}
.conv{font-style:normal;margin-left:auto;font-variant-numeric:tabular-nums;font-weight:600}
.conv.pos{color:var(--grn)}.conv.neg{color:var(--red)}.conv.neu{color:var(--mut)}
.reason{font-size:12.5px;color:var(--mut);border-left:2px solid var(--line);padding:4px 0 4px 10px;
margin:4px 0 8px;white-space:pre-wrap}
.toolbar{display:flex;gap:8px;align-items:center;margin-bottom:10px}
.toolbar input{background:var(--card);border:1px solid var(--line);border-radius:8px;color:var(--tx);
padding:7px 10px;width:240px;font-size:13px}
.toolbar button{background:var(--card);border:1px solid var(--line);border-radius:8px;color:var(--mut);
padding:7px 10px;cursor:pointer;font-size:12px}
.toolbar button:hover{color:var(--tx)}
footer{color:var(--mut);font-size:12px;margin-top:40px;border-top:1px solid var(--line);padding-top:14px}
"""

_JS = """
const tip=document.getElementById('tip');
document.querySelectorAll('svg.chart').forEach(svg=>{
  svg.addEventListener('mousemove',e=>{
    const d=svg.dataset.points?JSON.parse(svg.dataset.points):null;if(!d)return;
    const r=svg.getBoundingClientRect();const x=(e.clientX-r.left)/r.width;
    const i=Math.max(0,Math.min(d.dates.length-1,Math.round(x*(d.dates.length-1))));
    tip.style.display='block';tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY+14)+'px';
    tip.textContent=d.dates[i]+'\\n'+d.lines.map(l=>l.name+': '+l.fmt(l.values[i])).join('\\n');
  });
  svg.addEventListener('mouseleave',()=>tip.style.display='none');
});
function filterSess(q){q=q.toUpperCase();
  document.querySelectorAll('.sess').forEach(s=>{
    s.style.display=(!q||s.textContent.toUpperCase().includes(q))?'':'none';});}
function allSess(open){document.querySelectorAll('.sess').forEach(s=>s.open=open);}
"""


def render(data: dict) -> str:
    m = data["metrics"]
    dates, nav, bnav = data["dates"], data["nav"], data["benchmark_nav"]
    ret_series = [v / data["capital"] for v in nav]
    bret_series = [v / data["capital"] for v in bnav]
    equity = _line_chart(
        [("fund", ret_series, "#3fb950"), ("benchmark", bret_series, "#8b949e")],
        dates, y_fmt=lambda v: _pct(v - 1),
    )
    dd_series = _drawdown_series(nav)
    dd = _drawdown_chart(dd_series, dates)
    gross, net = _exposure_series(data["records"])
    exposure = _line_chart(
        [("gross", gross, "#d29922"), ("net", net, "#58a6ff")],
        dates, y_fmt=_pct, height=200,
    )

    def card(label: str, value: str, cls: str = "", sub: str = "") -> str:
        sub_h = f" <small>{_H(sub)}</small>" if sub else ""
        return f"<div class='card'><div class='l'>{_H(label)}</div><div class='v {cls}'>{value}{sub_h}</div></div>"

    cards = "".join([
        card("Return", _pct(m["total_return_pct"]), "pos" if m["total_return_pct"] >= 0 else "neg"),
        card(data["benchmark"], _pct(m["benchmark_return_pct"]), "", f"excess {_pct(m['excess_return_pct'])}"),
        card("Annualized", _pct(m["annualized_return_pct"])),
        card("Sharpe", f"{m['sharpe_ratio']:.2f}"),
        card("Max drawdown", _pct(-abs(m["max_drawdown_pct"])), "neg"),
        card("Rebalances", str(m["n_cycles"]), "", f"{m['n_orders']} orders"),
    ])

    def _points(lines: list[tuple[str, list[float], str]]) -> str:
        return _H(json.dumps({
            "dates": dates,
            "lines": [{"name": n, "values": v, "fmt": f} for n, v, f in lines],
        }))

    equity = equity.replace("<svg ", f"<svg data-points='{_points([('fund', ret_series, 'pct'), (data['benchmark'], bret_series, 'pct')])}' ", 1)
    dd = dd.replace("<svg ", f"<svg data-points='{_points([('drawdown', dd_series, 'pct0')])}' ", 1)
    exposure = exposure.replace("<svg ", f"<svg data-points='{_points([('gross', gross, 'pct0'), ('net', net, 'pct0')])}' ", 1)
    # JS value formatter: pct = return vs capital, pct0 = plain fraction
    js = _JS.replace(
        "l.fmt(l.values[i])",
        "(l.fmt==='pct'?((l.values[i]-1)*100).toFixed(2)+'%'"
        ":(l.fmt==='pct0'?(l.values[i]*100).toFixed(1)+'%':l.values[i]))",
    )

    lims = "".join(f"<li>{_H(x)}</li>" for x in data.get("limitations", []))
    lim_html = f"<footer><b>Limitations</b><ul>{lims}</ul><p class='muted'>generated by hedge_fund.backtesting.report · {data.get('data_source','')} · {data.get('currency','')}</p></footer>" if lims else ""

    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<title>{_H(data['fund'])} · backtest {_H(data['start'])}→{_H(data['end'])}</title>
<style>{_CSS}</style></head><body><div class="wrap">
<h1>{_H(data['fund'])}</h1>
<div class="sub">{_H(data['start'])} → {_H(data['end'])} · {len(dates)} sessions ·
{_H(data['rebalance'])} rebalance · {_brl(data['capital'])} ·
{len(data['universe'])} tickers vs {_H(data['benchmark'])}</div>
<div class="cards">{cards}</div>
<h2>Equity curve</h2>{equity}
<h2>Drawdown</h2>{dd}
<h2>Exposure · gross vs net</h2>{exposure}
<h2>Target weights · ticker × session</h2>
{_heatmap(data['records'], data['universe'])}
<h2>Sessions</h2>
<div class="toolbar">
<input placeholder="filter sessions (ticker, date…)" oninput="filterSess(this.value)">
<button onclick="allSess(true)">expand all</button>
<button onclick="allSess(false)">collapse all</button>
</div>
{_sessions_html(data['records'])}
{lim_html}
</div><div class="tip" id="tip"></div>
<script>{js}</script></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m hedge_fund.backtesting.report",
        description="Render a backtest result JSON as a self-contained HTML report.",
    )
    parser.add_argument("result", help="path to a research/*.json result")
    parser.add_argument("-o", "--out", help="output HTML path (default: alongside the JSON)")
    args = parser.parse_args()
    src = Path(args.result)
    data = json.loads(src.read_text())
    out = Path(args.out) if args.out else src.with_suffix(".html")
    out.write_text(render(data))
    print(out)


if __name__ == "__main__":
    main()
