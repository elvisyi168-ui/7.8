"""資料層：抓 Binance USDT 永續 K 線與資金費率（公開 API，免金鑰），本地 CSV 快取。"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

BASE = "https://fapi.binance.com"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INTERVAL_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def _get(path: str, params: dict, retries: int = 4):
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 ** (i + 1))


def fetch_klines(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """抓最近 N 天 K 線（自動分頁）。"""
    step = INTERVAL_MIN[interval] * 60_000
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    rows = []
    while start < end:
        batch = _get("/fapi/v1/klines", {"symbol": symbol, "interval": interval,
                                         "startTime": start, "limit": 1500})
        if not batch:
            break
        rows += batch
        start = batch[-1][0] + step
        time.sleep(0.2)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume",
                                     "close_ts", "qv", "n", "tb", "tq", "ig"])
    df = df[["ts", "open", "high", "low", "close", "volume"]].astype(float)
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df = df.drop_duplicates("ts").set_index("ts").sort_index()
    # 最後一根可能尚未收盤，丟掉以免用到未完成資料
    now = pd.Timestamp.now(tz="UTC")
    return df[df.index + pd.Timedelta(minutes=INTERVAL_MIN[interval]) <= now]


def fetch_funding(symbol: str, days: int) -> pd.Series:
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    rows = []
    while start < end:
        batch = _get("/fapi/v1/fundingRate", {"symbol": symbol, "startTime": start, "limit": 1000})
        if not batch:
            break
        rows += batch
        start = batch[-1]["fundingTime"] + 1
        if len(batch) < 1000:
            break
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({pd.to_datetime(r["fundingTime"], unit="ms", utc=True).floor("min"): float(r["fundingRate"])
                   for r in rows})
    return s.sort_index()


def load(symbol: str, interval: str, days: int, refresh: bool = False) -> pd.DataFrame:
    """優先讀快取；refresh=True 重新下載。回傳含 funding 欄位（無則 NaN）。"""
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{symbol}_{interval}_{days}d.csv"
    if path.exists() and not refresh:
        df = pd.read_csv(path, index_col="ts", parse_dates=["ts"])
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    df = fetch_klines(symbol, interval, days)
    try:
        f = fetch_funding(symbol, days)
        df["funding"] = f.reindex(df.index)
    except Exception:
        df["funding"] = np.nan
    df.to_csv(path, index_label="ts")
    return df


def synthetic(days: int = 60, interval: str = "5m", seed: int = 7, start_price: float = 60_000) -> pd.DataFrame:
    """模擬資料（離線測試／示範用）：趨勢與盤整交替 + 日內量能週期。"""
    rng = np.random.default_rng(seed)
    m = INTERVAL_MIN[interval]
    n = days * 1440 // m
    idx = pd.date_range("2026-01-01", periods=n, freq=f"{m}min", tz="UTC")
    regime = np.repeat(rng.choice([-1, 0, 1], size=n // 288 + 1, p=[.25, .5, .25]), 288)[:n]
    hour = idx.hour.values
    vol_mult = 1 + 0.8 * np.isin(hour, [0, 7, 8, 13, 14])
    sigma = 0.0012 * vol_mult
    drift = regime * 0.00012
    rets = drift + sigma * rng.standard_t(4, n) / np.sqrt(2)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.r_[start_price, close[:-1]]
    wick = np.abs(rng.normal(0, sigma, (2, n))) * close
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    volume = rng.gamma(2, 50, n) * vol_mult * (1 + 20 * np.abs(rets))
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
    df.index.name = "ts"
    df["funding"] = np.where((idx.minute == 0) & np.isin(hour, [0, 8, 16]), 0.0001, np.nan)
    return df
