"""績效指標。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def max_consecutive(mask: pd.Series) -> int:
    best = run = 0
    for v in mask:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


def summary(res) -> dict:
    t, eq = res.trades, res.equity
    start = res.cfg["account"]["equity"]
    dd = eq / eq.cummax() - 1
    daily = eq.resample("D").last().pct_change().dropna()
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if len(daily) > 1 and daily.std() > 0 else 0.0
    out = {"起始資金": start, "期末資金": round(float(eq.iloc[-1]), 2),
           "總報酬%": round((eq.iloc[-1] / start - 1) * 100, 2),
           "最大回撤%": round(dd.min() * 100, 2), "夏普(年化)": round(float(sharpe), 2),
           "交易次數": len(t), "被風控擋下": res.rejected}
    if len(t) == 0:
        return out
    wins, losses = t[t.pnl > 0], t[t.pnl <= 0]
    gp, gl = wins.pnl.sum(), -losses.pnl.sum()
    out.update({
        "勝率%": round(len(wins) / len(t) * 100, 1),
        "獲利因子": round(gp / gl, 2) if gl > 0 else float("inf"),
        "期望值(R)": round(t.R.mean(), 3),
        "平均賺(R)": round(wins.R.mean(), 2) if len(wins) else 0,
        "平均賠(R)": round(losses.R.mean(), 2) if len(losses) else 0,
        "最大連虧": max_consecutive(t.pnl <= 0),
        "手續費合計": round(t.fees.sum(), 2),
        "資金費合計": round(t.funding.sum(), 2),
        "報酬/回撤": round(out["總報酬%"] / abs(out["最大回撤%"]), 2) if out["最大回撤%"] < 0 else 0,
    })
    return out


def breakdown(t: pd.DataFrame, by: str) -> pd.DataFrame:
    if t.empty:
        return pd.DataFrame()
    g = t.copy()
    if by == "hour":
        g["hour"] = pd.to_datetime(g.entry_ts).dt.hour
    return g.groupby(by).agg(次數=("pnl", "size"), 勝率=("pnl", lambda x: round((x > 0).mean() * 100, 1)),
                             期望R=("R", lambda x: round(x.mean(), 3)), 損益=("pnl", lambda x: round(x.sum(), 2)))
