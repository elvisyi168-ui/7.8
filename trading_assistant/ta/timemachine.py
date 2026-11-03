"""Time Machine：下單前，先看這一單的「誠實機率」。

1. 蒙地卡羅：把剩下的走勢重組 N 次。每一步都從該商品「過去同一時段」的真實 K 線
   隨機抽一根（保留時段特性：開盤波動大、午盤清淡），套用到目前價格上，看先碰停利還是停損。
2. 歷史重播：同一套策略規則在過去每次觸發的真實結果（用回測引擎，含成本）。
3. 綜合判定：有優勢／接近擲硬幣／負期望。
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from . import backtest, strategies
from .markets import hhmm, roundtrip_rate


def _slots_ahead(all_slots: np.ndarray, entry_slot: int, bar: int, cfg: dict) -> list[int]:
    mk, ex = cfg["market"], cfg["execution"]
    H = ex["max_hold_bars"] + 1
    flat = hhmm(mk.get("flat_by", ""))
    if mk.get("sessions"):  # 股票：只走到收盤前平倉時間
        ahead = [s for s in all_slots if s >= entry_slot and (flat is None or s < flat)]
        return ahead[:H]
    cut = mk.get("day_cut_hour", 0) * 60
    out = []
    for k in range(H):
        s = (entry_slot + k * bar) % 1440
        if k > 0 and ex.get("flat_at_day_cut", True) and s == cut:
            break
        out.append(s)
    return out


def simulate(hist: pd.DataFrame, cfg: dict, side: int, entry: float, stop: float, target: float,
             entry_ts: pd.Timestamp, runs: int | None = None, seed: int | None = None) -> dict:
    tmc = cfg.get("timemachine", {})
    runs = runs or tmc.get("runs", 10000)
    rng = np.random.default_rng(seed if seed is not None else tmc.get("seed", 19))
    tz = cfg["market"].get("tz", "UTC")
    bar = strategies.bar_minutes(hist)

    local = hist.index.tz_convert(tz)
    slot = (local.hour * 60 + local.minute).to_numpy()
    o, h, l, c = (hist[k].to_numpy() for k in ("open", "high", "low", "close"))
    rh, rl, rc = h / o, l / o, c / o
    gap = np.r_[1.0, o[1:] / c[:-1]]
    by_slot = {s: np.flatnonzero(slot == s) for s in np.unique(slot)}

    el = entry_ts.tz_convert(tz)
    entry_slot = el.hour * 60 + el.minute
    path = [s for s in _slots_ahead(np.array(sorted(by_slot)), entry_slot, bar, cfg) if s in by_slot]
    if not path:
        return {"ok": False, "reason": "歷史資料沒有對應時段"}

    P = np.full(runs, entry, dtype=float)
    status = np.zeros(runs, dtype=int)     # 0 未出場 / 1 停利 / -1 停損
    exit_px = np.full(runs, np.nan)
    for k, s in enumerate(path):
        pick = rng.choice(by_slot[s], runs)
        op = P if k == 0 else P * gap[pick]
        hi, lo, cl = op * rh[pick], op * rl[pick], op * rc[pick]
        live = status == 0
        if side > 0:
            hit_s, hit_t = live & (lo <= stop), live & (hi >= target)
            ps, pt = np.minimum(op, stop), np.maximum(op, target)
        else:
            hit_s, hit_t = live & (hi >= stop), live & (lo <= target)
            ps, pt = np.maximum(op, stop), np.minimum(op, target)
        hit_t &= ~hit_s                     # 同根都碰到 → 算停損
        status[hit_s], exit_px[hit_s] = -1, ps[hit_s]
        status[hit_t], exit_px[hit_t] = 1, pt[hit_t]
        P = cl
    open_ = status == 0
    exit_px[open_] = P[open_]

    risk = abs(entry - stop)
    cost_r = entry * roundtrip_rate(cfg["costs"]) / risk
    R = (exit_px - entry) * side / risk - cost_r
    close_R = (P - entry) * side / risk
    return {"ok": True, "runs": runs, "bars_ahead": len(path),
            "days_per_slot": int(np.median([len(by_slot[s]) for s in path])),
            "p_target": float((status == 1).mean()), "p_stop": float((status == -1).mean()),
            "p_open": float(open_.mean()), "mean_R": float(R.mean()), "median_R": float(np.median(R)),
            "p10_close_R": float(np.percentile(close_R, 10)), "p90_close_R": float(np.percentile(close_R, 90)),
            "cost_R": float(cost_r)}


def history(hist: pd.DataFrame, cfg: dict, strategy: str) -> dict:
    """同一策略在過去每次觸發的真實結果（含成本、同樣的保守撮合）。"""
    c2 = copy.deepcopy(cfg)
    for name in c2["strategy"]:
        if isinstance(c2["strategy"][name], dict):
            c2["strategy"][name]["enabled"] = name == strategy
    t = backtest.run(hist, c2).trades
    if t.empty:
        return {"n": 0}
    return {"n": len(t), "target": int((t.reason == "停利").sum()), "stop": int((t.reason == "停損").sum()),
            "other": int((~t.reason.isin(["停利", "停損"])).sum()), "mean_R": float(t.R.mean()),
            "win_rate": float((t.pnl > 0).mean())}


def verdict(sim: dict, hist: dict, min_edge: float = 0.0) -> tuple[str, str]:
    edges = [sim["mean_R"]] if sim.get("ok") else []
    if hist.get("n", 0) >= 10:
        edges.append(hist["mean_R"])
    if not edges:
        return "資料不足", "insufficient"
    if min(edges) < min_edge - 0.05:
        return "負期望，不建議 ❌", "negative"
    if min(edges) > min_edge + 0.1:
        return "有優勢 ✅", "edge"
    return "接近擲硬幣 ⚖️", "coinflip"


def run(hist: pd.DataFrame, cfg: dict, plan: dict) -> dict:
    sim = simulate(hist, cfg, plan["side"], plan["entry"], plan["stop"], plan["target"], plan["entry_ts"])
    hst = history(hist, cfg, plan["strategy"])
    text, code = verdict(sim, hst, cfg.get("timemachine", {}).get("min_edge_r", 0.0))
    return {"sim": sim, "history": hst, "verdict": text, "code": code}


def format_report(tm: dict) -> str:
    s, hst = tm["sim"], tm["history"]
    lines = ["⏳ Time Machine"]
    if s.get("ok"):
        lines += [f"  模擬 {s['runs']:,} 次（往後 {s['bars_ahead']} 根，每時段約 {s['days_per_slot']} 天樣本）",
                  f"  先到停利 {s['p_target']:.1%}｜先到停損 {s['p_stop']:.1%}｜時間到未分勝負 {s['p_open']:.1%}",
                  f"  平均 {s['mean_R']:+.2f}R（已扣成本 {s['cost_R']:.2f}R）｜結束時 10%～90% 區間 "
                  f"{s['p10_close_R']:+.2f}R ~ {s['p90_close_R']:+.2f}R"]
    else:
        lines.append(f"  模擬略過：{s.get('reason')}")
    if hst.get("n"):
        lines.append(f"  歷史重播：觸發 {hst['n']} 次，停利 {hst['target']}、停損 {hst['stop']}、其他 {hst['other']}，"
                     f"勝率 {hst['win_rate']:.0%}，平均 {hst['mean_R']:+.2f}R")
    else:
        lines.append("  歷史重播：期間內沒有觸發紀錄")
    lines.append(f"  判定：{tm['verdict']}")
    return "\n".join(lines)
