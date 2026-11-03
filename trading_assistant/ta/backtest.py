"""回測引擎（逐根 K 線、事件驅動），台股／美股／加密通用。

保守假設：
- 訊號於 K 收盤確認，下一根開盤市價進場（含滑價 + 手續費）
- 同一根同時碰到停損與停利 → 算停損；跳空越過停損 → 以開盤價成交
- 停利為限價單（maker 費、無滑價）；賣出邊加收稅費（台股證交稅）
- 加密：計入資金費率、強平檢查；股票：收盤前（flat_by）強制平倉、最後進場時間後不開新倉
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators, strategies
from .markets import fee, hhmm
from .risk import size_position


@dataclass
class Result:
    trades: pd.DataFrame
    equity: pd.Series
    cfg: dict = field(repr=False, default_factory=dict)
    rejected: int = 0


def run(df_raw: pd.DataFrame, cfg: dict, signals: pd.DataFrame | None = None) -> Result:
    df = indicators.add_all(df_raw, cfg)
    sig = signals if signals is not None else strategies.combined(df, cfg)
    acc, cost, ex, mk = cfg["account"], cfg["costs"], cfg["execution"], cfg["market"]
    slip = cost["slippage_bps"] / 10_000
    default_fr = cost["default_funding_rate_pct"] / 100
    use_funding = mk.get("funding", False)

    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    ts = df.index
    local = ts.tz_convert(mk.get("tz", "UTC"))
    mod = (local.hour * 60 + local.minute).to_numpy()
    flat_min, last_min = hhmm(mk.get("flat_by", "")), hhmm(mk.get("last_entry", ""))
    fr = df["funding"].to_numpy() if "funding" in df else np.full(len(df), np.nan)
    is_funding_bar = (ts.minute == 0) & np.isin(ts.hour, [0, 8, 16])
    dkey = indicators.day_key(ts, mk.get("day_cut_hour", 0), mk.get("tz", "UTC")).to_numpy()
    s_sig, s_stop, s_tgt, s_name = (sig[k].to_numpy() for k in ("signal", "stop", "target", "strategy"))

    equity = float(acc["equity"])
    eq_curve = np.empty(len(df))
    trades, rejected = [], 0
    pos = None
    cur_day, day_start_eq, day_trades = None, equity, 0

    def close_pos(i, price, reason, maker=False):
        nonlocal equity, pos
        p = pos
        gross = (price - p["entry"]) * p["qty"] * p["side"]
        f = fee(cost, price, p["qty"], p["side"] > 0, maker)
        if reason == "強平":
            gross, f = -p["margin"], 0.0
        pnl = gross - f - p["entry_fee"] + p["funding"]
        equity += gross - f  # 進場費、資金費已在發生時入帳
        trades.append({"entry_ts": p["ts"], "exit_ts": ts[i], "side": "多" if p["side"] > 0 else "空",
                       "strategy": p["strategy"], "entry": p["entry"], "exit": price, "qty": p["qty"],
                       "stop": p["stop"], "target": p["target"], "pnl": pnl,
                       "fees": f + p["entry_fee"], "funding": p["funding"],
                       "R": pnl / p["risk"] if p["risk"] else 0.0, "bars": i - p["i"], "reason": reason})
        pos = None

    for i in range(len(df)):
        new_day = dkey[i] != cur_day
        if new_day:
            if pos is not None and cur_day is not None and (ex["flat_at_day_cut"] or flat_min is not None):
                close_pos(i, o[i] * (1 - slip * pos["side"]), "跨日平倉")
            cur_day, day_start_eq, day_trades = dkey[i], equity, 0

        if pos is not None and flat_min is not None and mod[i] >= flat_min:
            close_pos(i, o[i] * (1 - slip * pos["side"]), "收盤前平倉")

        if pos is not None and use_funding and is_funding_bar[i]:
            rate = fr[i] if not np.isnan(fr[i]) else default_fr
            f = -pos["side"] * rate * pos["qty"] * o[i]
            pos["funding"] += f
            equity += f

        # 進場：前一根有訊號、同一交易日、空手、在可進場時段、未觸發日風控
        if pos is None and i > 0 and s_sig[i - 1] != 0 and not new_day:
            side = int(s_sig[i - 1])
            stop, tgt = s_stop[i - 1], s_tgt[i - 1]
            entry = o[i] * (1 + slip * side)
            day_dd = (equity - day_start_eq) / day_start_eq * 100
            late = (last_min is not None and mod[i] >= last_min) or (flat_min is not None and mod[i] >= flat_min)
            gap_bad = (side > 0 and (entry <= stop or entry >= tgt)) or (side < 0 and (entry >= stop or entry <= tgt))
            if day_dd <= -acc["daily_loss_limit_pct"] or day_trades >= acc["max_trades_per_day"] or gap_bad or late:
                rejected += 1
            else:
                sz = size_position(equity, entry, stop, side, cfg)
                if not sz.ok:
                    rejected += 1
                else:
                    efee = fee(cost, entry, sz.qty, side < 0)
                    equity -= efee
                    pos = {"i": i, "ts": ts[i], "side": side, "entry": entry, "qty": sz.qty, "stop": stop,
                           "target": tgt, "liq": sz.liq_price, "margin": sz.margin, "entry_fee": efee,
                           "funding": 0.0, "risk": sz.risk_amount, "strategy": s_name[i - 1]}
                    day_trades += 1

        if pos is not None:
            sd, stp, tg = pos["side"], pos["stop"], pos["target"]
            liq = pos["liq"]
            if liq and ((sd > 0 and o[i] <= liq) or (sd < 0 and o[i] >= liq)):
                close_pos(i, liq, "強平")
            elif (sd > 0 and l[i] <= stp) or (sd < 0 and h[i] >= stp):
                fill = min(o[i], stp) if sd > 0 else max(o[i], stp)
                close_pos(i, fill * (1 - slip * sd), "停損")
            elif (sd > 0 and h[i] >= tg) or (sd < 0 and l[i] <= tg):
                fill = max(o[i], tg) if sd > 0 else min(o[i], tg)
                close_pos(i, fill, "停利", maker=True)
            elif i - pos["i"] >= ex["max_hold_bars"]:
                close_pos(i, c[i] * (1 - slip * sd), "時間到")

        unreal = (c[i] - pos["entry"]) * pos["qty"] * pos["side"] if pos is not None else 0.0
        eq_curve[i] = equity + unreal

    if pos is not None:
        close_pos(len(df) - 1, c[-1], "回測結束")
        eq_curve[-1] = equity

    return Result(pd.DataFrame(trades), pd.Series(eq_curve, index=ts, name="equity"), cfg, rejected)
