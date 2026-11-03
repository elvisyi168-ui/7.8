"""策略層：每根 K 收盤後產生訊號。輸出欄位：
signal (1 多 / -1 空 / 0 無)、stop、target、strategy。引擎於「下一根開盤」成交。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _bar_minutes(df: pd.DataFrame) -> int:
    return int((df.index[1] - df.index[0]).total_seconds() // 60)


def orb(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    """時段開盤區間突破：區間形成後，帶量 + 趨勢夠強 的收盤突破。每時段每方向限一次。"""
    bar = pd.Timedelta(minutes=_bar_minutes(df))
    idx = df.index
    days = pd.date_range(idx[0].floor("D") - pd.Timedelta(days=1), idx[-1].ceil("D"), freq="D")
    anchors = pd.DatetimeIndex(sorted(d + pd.Timedelta(hours=h) for d in days for h in p["session_hours_utc"]))
    pos = anchors.searchsorted(idx, side="right") - 1
    anchor = pd.Series(anchors[pos], index=idx)
    elapsed = pd.TimedeltaIndex(idx - pd.DatetimeIndex(anchor.values).tz_localize(None).tz_localize(idx.tz))
    rng_len = pd.Timedelta(minutes=p["range_minutes"])
    in_range = (elapsed + bar) <= rng_len
    tradable = (elapsed >= rng_len) & (elapsed < rng_len + pd.Timedelta(minutes=p["trade_window_minutes"]))

    hi = df["high"].where(in_range).groupby(anchor).cummax()
    lo = df["low"].where(in_range).groupby(anchor).cummin()
    hi = hi.groupby(anchor).ffill()
    lo = lo.groupby(anchor).ffill()

    ok = tradable & (df["vol_ratio"] >= p["min_volume_ratio"]) & (df["adx"] >= p["min_adx"])
    long_b = ok & (df["close"] > hi)
    short_b = ok & (df["close"] < lo)
    # 每時段每方向只取第一次
    long_b &= long_b.astype(int).groupby(anchor).cumsum() == 1
    short_b &= short_b.astype(int).groupby(anchor).cumsum() == 1

    out = pd.DataFrame(index=idx)
    out["signal"] = np.where(long_b, 1, np.where(short_b, -1, 0))
    risk = df["atr"] * p["atr_stop_mult"]
    out["stop"] = np.where(long_b, df["close"] - risk, np.where(short_b, df["close"] + risk, np.nan))
    out["target"] = np.where(long_b, df["close"] + risk * p["reward_risk"],
                             np.where(short_b, df["close"] - risk * p["reward_risk"], np.nan))
    out["strategy"] = np.where(out["signal"] != 0, "orb", "")
    return out


def vwap_revert(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    """VWAP 回歸：盤整市況下，價格偏離 VWAP 超過 N×ATR 後「收回」門檻內才進場，目標 VWAP。"""
    dev = (df["close"] - df["vwap"]) / df["atr"]
    k = p["entry_dev_atr"]
    calm = df["adx"] < p["max_adx"]
    long_s = calm & (dev.shift(1) < -k) & (dev >= -k) & (df["close"] > df["open"])
    short_s = calm & (dev.shift(1) > k) & (dev <= k) & (df["close"] < df["open"])
    risk = df["atr"] * p["atr_stop_mult"]
    reward = (df["vwap"] - df["close"]).abs()
    good_rr = reward >= risk * p["min_reward_risk"]
    long_s &= good_rr
    short_s &= good_rr

    out = pd.DataFrame(index=df.index)
    out["signal"] = np.where(long_s, 1, np.where(short_s, -1, 0))
    out["stop"] = np.where(long_s, df["close"] - risk, np.where(short_s, df["close"] + risk, np.nan))
    out["target"] = np.where(out["signal"] != 0, df["vwap"], np.nan)
    out["strategy"] = np.where(out["signal"] != 0, "vwap", "")
    return out


REGISTRY = {"orb": orb, "vwap": vwap_revert}


def combined(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """合併啟用中的策略；同一根若多策略衝突（一多一空）則放棄，避免左右互搏。"""
    parts = [fn(df, cfg["strategy"][name]) for name, fn in REGISTRY.items()
             if cfg["strategy"].get(name, {}).get("enabled")]
    if not parts:
        raise ValueError("沒有啟用任何策略")
    out = parts[0].copy()
    for p in parts[1:]:
        take = (out["signal"] == 0) & (p["signal"] != 0)
        conflict = (out["signal"] != 0) & (p["signal"] != 0) & (out["signal"] != p["signal"])
        out.loc[take] = p.loc[take]
        out.loc[conflict, ["signal", "stop", "target", "strategy"]] = [0, np.nan, np.nan, ""]
    return out
