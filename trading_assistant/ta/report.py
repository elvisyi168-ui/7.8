"""產出單一 HTML 回測報告（內嵌 SVG 圖，免安裝 matplotlib，手機可看，支援深色模式）。"""
from __future__ import annotations

import html
from pathlib import Path

import pandas as pd

from . import metrics


def _svg_line(s: pd.Series, h: int = 220, color: str = "var(--accent)", fill: bool = False) -> str:
    s = s.dropna()
    if len(s) > 1500:
        s = s.iloc[:: len(s) // 1500]
    w = 1000
    lo, hi = float(s.min()), float(s.max())
    span = hi - lo or 1
    pts = [(i / (len(s) - 1) * w, h - (v - lo) / span * (h - 10) - 5) for i, v in enumerate(s.to_numpy())]
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f'<path d="{d} L{w},{h} L0,{h} Z" fill="{color}" opacity=".15"/>' if fill else ""
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="chart">{area}'
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>'
            f'<div class="axis"><span>{s.index[0]:%Y-%m-%d}</span><span>{lo:,.2f} ~ {hi:,.2f}</span>'
            f'<span>{s.index[-1]:%Y-%m-%d}</span></div>')


def _table(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "<p class='muted'>無資料</p>"
    return '<div class="tw">' + df.to_html(border=0, classes="t", float_format=lambda x: f"{x:,.4g}") + "</div>"


def build(res, path: str | Path, title: str = "回測報告") -> Path:
    s = metrics.summary(res)
    eq = res.equity
    dd = (eq / eq.cummax() - 1) * 100
    cards = "".join(f'<div class="card"><div class="k">{html.escape(k)}</div><div class="v">{v}</div></div>'
                    for k, v in s.items())
    t = res.trades
    recent = t.tail(50).iloc[::-1].copy() if not t.empty else t
    if not recent.empty:
        recent = recent[["entry_ts", "exit_ts", "strategy", "side", "entry", "exit", "pnl", "R", "reason"]]
        recent["entry_ts"] = pd.to_datetime(recent.entry_ts).dt.strftime("%m/%d %H:%M")
        recent["exit_ts"] = pd.to_datetime(recent.exit_ts).dt.strftime("%m/%d %H:%M")
    doc = f"""<!doctype html><html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>
:root{{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e4e4df;--accent:#2563eb;--neg:#dc2626}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141413;--fg:#ececea;--muted:#9a9a94;--card:#1f1f1d;--line:#33332f;--accent:#60a5fa;--neg:#f87171}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,"Noto Sans TC",sans-serif;margin:0;padding:24px 16px}}
main{{max-width:1080px;margin:auto}} h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:28px 0 10px}}
.muted{{color:var(--muted)}} .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}}
.k{{font-size:12px;color:var(--muted)}} .v{{font-size:18px;font-weight:600;font-variant-numeric:tabular-nums}}
.box{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}}
.chart{{width:100%;height:220px;display:block}} .axis{{display:flex;justify-content:space-between;font-size:12px;color:var(--muted)}}
.tw{{overflow-x:auto}} .t{{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}}
.t th,.t td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}
</style></head><body><main>
<h1>{html.escape(title)}</h1><p class="muted">含手續費、滑價、資金費率；同根 K 先算停損。回測不代表未來績效。</p>
<div class="grid">{cards}</div>
<h2>權益曲線</h2><div class="box">{_svg_line(eq, fill=True)}</div>
<h2>回撤 %</h2><div class="box">{_svg_line(dd, 140, "var(--neg)", True)}</div>
<h2>各策略</h2>{_table(metrics.breakdown(t, "strategy"))}
<h2>各進場時段（UTC 小時）</h2>{_table(metrics.breakdown(t, "hour"))}
<h2>出場原因</h2>{_table(metrics.breakdown(t, "reason"))}
<h2>最近 50 筆交易</h2>{_table(recent)}
</main></body></html>"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc, encoding="utf-8")
    return p
