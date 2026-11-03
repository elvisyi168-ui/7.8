"""技術指標（全部只用當根與過去資料，不偷看未來）。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rma(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return rma(tr, n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * rma(pd.Series(plus, df.index), n) / a
    mdi = 100 * rma(pd.Series(minus, df.index), n) / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return rma(dx, n)


def day_key(index: pd.DatetimeIndex, cut_hour: int = 0) -> pd.Series:
    return pd.Series((index - pd.Timedelta(hours=cut_hour)).floor("D"), index=index)


def session_vwap(df: pd.DataFrame, cut_hour: int = 0) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    key = day_key(df.index, cut_hour)
    pv = (tp * df["volume"]).groupby(key).cumsum()
    v = df["volume"].groupby(key).cumsum()
    return pv / v


def volume_ratio(df: pd.DataFrame, n: int = 20) -> pd.Series:
    return df["volume"] / df["volume"].rolling(n).mean().shift(1)


def add_all(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    ic = cfg["indicators"]
    out = df.copy()
    out["atr"] = atr(out, ic["atr_period"])
    out["adx"] = adx(out, ic["adx_period"])
    out["vwap"] = session_vwap(out, cfg["market"]["day_cut_hour_utc"])
    out["vol_ratio"] = volume_ratio(out, ic["volume_lookback"])
    return out
