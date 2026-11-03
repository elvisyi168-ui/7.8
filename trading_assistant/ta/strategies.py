"""策略層：每根 K 收盤後產生訊號。輸出欄位：
signal (1 多 / -1 空 / 0 無)、stop、target、strategy、or_high、or_low。引擎於「下一根開盤」成交。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def bar_minutes(df: pd.DataFrame) -> int:
    return int(pd.Series(df.index).diff().median().total_seconds() // 60)


def _anchors(idx: pd.DatetimeIndex, times: list[str], tz: str) -> pd.DatetimeIndex:
    """把「當地時間 HH:MM」轉成每天的 UTC 時間點（自動處理夏令時間）。"""
    local = idx.tz_convert(tz)
    days = pd.date_range(local[0].tz_localize(None).normalize() - pd.Timedelta(days=1),
                         local[-1].tz_localize(None).normalize() + pd.Timedelta(days=1), freq="D")
    naive = sorted(d + pd.Timedelta(hours=int(t[:2]), minutes=int(t[3:])) for d in days for t in times)
    a = pd.DatetimeIndex(naive).tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
    return a[~a.isna()].tz_convert(idx.tz)


def orb(df: pd.DataFrame, p: dict, mk: dict) -> pd.DataFrame:
    """開盤區間突破：區間形成後，帶量 + 趨勢夠強的收盤突破。每時段每方向限一次。"""
    bar = pd.Timedelta(minutes=bar_minutes(df))
    idx = df.index
    anchors = _anchors(idx, p["session_times"], mk.get("tz", "UTC"))
    pos = anchors.searchsorted(idx, side="right") - 1
    anchor_at = anchors[pos]
    anchor = pd.Series(anchor_at, index=idx)
    elapsed = idx - anchor_at
    rng_len = pd.Timedelta(minutes=p["range_minutes"])
    in_range = np.asarray((elapsed + bar) <= rng_len)
    tradable = np.asarray((elapsed >= rng_len) & (elapsed < rng_len + pd.Timedelta(minutes=p["trade_window_minutes"])))

    hi = df["high"].where(in_range).groupby(anchor).cummax().groupby(anchor).ffill()
    lo = df["low"].where(in_range).groupby(anchor).cummin().groupby(anchor).ffill()

    ok = tradable & (df["vol_ratio"] >= p["min_volume_ratio"]) & (df["adx"] >= p["min_adx"])
    long_b = ok & (df["close"] > hi)
    short_b = ok & (df["close"] < lo)
    long_b &= long_b.astype(int).groupby(anchor).cumsum() == 1
    short_b &= short_b.astype(int).groupby(anchor).cumsum() == 1

    mode = p.get("stop_mode", "atr")
    c = df["close"]
    if mode == "or_mid":
        mid = (hi + lo) / 2
        stop_l, stop_s = mid, mid
    elif mode == "or_low":
        stop_l, stop_s = lo, hi
    else:
        r = df["atr"] * p["atr_stop_mult"]
        stop_l, stop_s = c - r, c + r

    out = pd.DataFrame(index=idx)
    out["signal"] = np.where(long_b, 1, np.where(short_b, -1, 0))
    out["stop"] = np.where(long_b, stop_l, np.where(short_b, stop_s, np.nan))
    risk = (c - out["stop"]).abs()
    out["target"] = np.where(long_b, c + risk * p["reward_risk"], np.where(short_b, c - risk * p["reward_risk"], np.nan))
    bad = (out["signal"] != 0) & ~(risk > 0)
    out.loc[bad, ["signal", "stop", "target"]] = [0, np.nan, np.nan]
    out["strategy"] = np.where(out["signal"] != 0, "orb", "")
    out["or_high"], out["or_low"] = hi, lo
    return out


def vwap_revert(df: pd.DataFrame, p: dict, mk: dict) -> pd.DataFrame:
    """VWAP 回歸：盤整市況下，價格偏離 VWAP 超過 N×ATR 後收回門檻內才進場，目標 VWAP。"""
    dev = (df["close"] - df["vwap"]) / df["atr"]
    k = p["entry_dev_atr"]
    calm = df["adx"] < p["max_adx"]
    long_s = calm & (dev.shift(1) < -k) & (dev >= -k) & (df["close"] > df["open"])
    short_s = calm & (dev.shift(1) > k) & (dev <= k) & (df["close"] < df["open"])
    risk = df["atr"] * p["atr_stop_mult"]
    good_rr = (df["vwap"] - df["close"]).abs() >= risk * p["min_reward_risk"]
    long_s &= good_rr
    short_s &= good_rr

    out = pd.DataFrame(index=df.index)
    out["signal"] = np.where(long_s, 1, np.where(short_s, -1, 0))
    out["stop"] = np.where(long_s, df["close"] - risk, np.where(short_s, df["close"] + risk, np.nan))
    out["target"] = np.where(out["signal"] != 0, df["vwap"], np.nan)
    out["strategy"] = np.where(out["signal"] != 0, "vwap", "")
    out["or_high"] = out["or_low"] = np.nan
    return out


REGISTRY = {"orb": orb, "vwap": vwap_revert}
COLS = ["signal", "stop", "target", "strategy"]


def combined(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """合併啟用中的策略；同根一多一空則放棄；市場不允許放空時剔除空單。"""
    mk = cfg["market"]
    parts = [fn(df, cfg["strategy"][name], mk) for name, fn in REGISTRY.items()
             if cfg["strategy"].get(name, {}).get("enabled")]
    if not parts:
        raise ValueError("沒有啟用任何策略")
    out = parts[0].copy()
    for p in parts[1:]:
        take = (out["signal"] == 0) & (p["signal"] != 0)
        conflict = (out["signal"] != 0) & (p["signal"] != 0) & (out["signal"] != p["signal"])
        out.loc[take] = p.loc[take]
        out.loc[conflict, COLS] = [0, np.nan, np.nan, ""]
    if not mk.get("allow_short", True):
        out.loc[out["signal"] < 0, COLS] = [0, np.nan, np.nan, ""]
    return out
