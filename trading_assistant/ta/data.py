"""資料層：
- 加密：Binance USDT 永續公開 API（K 線 + 資金費率，免金鑰）
- 美股／台股：Yahoo Finance chart API（5m 最多約 60 天、1m 約 7 天；台股代號 2330.TW / xxxx.TWO）
本地 CSV 快取於 data/。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from .markets import hhmm

BINANCE = "https://fapi.binance.com"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INTERVAL_MIN = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
UA = {"User-Agent": "Mozilla/5.0 (trading-assistant)"}


def _get(url: str, retries: int = 4):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 ** (i + 1))


def _drop_unfinished(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC")
    return df[df.index + pd.Timedelta(minutes=INTERVAL_MIN[interval]) <= now]


# ---------- Binance ----------
def fetch_binance(symbol: str, interval: str, days: int) -> pd.DataFrame:
    step = INTERVAL_MIN[interval] * 60_000
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    rows = []
    while start < end:
        q = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "startTime": start, "limit": 1500})
        batch = _get(f"{BINANCE}/fapi/v1/klines?{q}")
        if not batch:
            break
        rows += batch
        start = batch[-1][0] + step
        time.sleep(0.2)
    df = pd.DataFrame([r[:6] for r in rows], columns=["ts", "open", "high", "low", "close", "volume"]).astype(float)
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df = df.drop_duplicates("ts").set_index("ts").sort_index()
    try:
        df["funding"] = fetch_funding(symbol, days).reindex(df.index)
    except Exception:
        df["funding"] = np.nan
    return _drop_unfinished(df, interval)


def fetch_funding(symbol: str, days: int) -> pd.Series:
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    rows = []
    while start < end:
        q = urllib.parse.urlencode({"symbol": symbol, "startTime": start, "limit": 1000})
        batch = _get(f"{BINANCE}/fapi/v1/fundingRate?{q}")
        if not batch:
            break
        rows += batch
        start = batch[-1]["fundingTime"] + 1
        if len(batch) < 1000:
            break
    s = pd.Series({pd.to_datetime(r["fundingTime"], unit="ms", utc=True).floor("min"): float(r["fundingRate"])
                   for r in rows}, dtype=float)
    return s.sort_index()


# ---------- Yahoo ----------
def parse_yahoo(j: dict) -> pd.DataFrame:
    res = j["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({k: q[k] for k in ("open", "high", "low", "close", "volume")},
                      index=pd.to_datetime(res["timestamp"], unit="s", utc=True), dtype=float)
    df.index.name = "ts"
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0)
    df["funding"] = np.nan
    return df[~df.index.duplicated()].sort_index()


def fetch_yahoo(symbol: str, interval: str, days: int) -> pd.DataFrame:
    cap = 7 if interval == "1m" else 60
    days = min(days, cap)
    q = urllib.parse.urlencode({"interval": interval, "range": f"{days}d"})
    return _drop_unfinished(parse_yahoo(_get(f"{YAHOO}{urllib.parse.quote(symbol)}?{q}")), interval)


FETCHERS = {"binance": fetch_binance, "yahoo": fetch_yahoo}


def fetch(mk: dict, symbol: str, days: int) -> pd.DataFrame:
    return FETCHERS[mk["source"]](symbol, mk["interval"], days)


def load(mk: dict, symbol: str, days: int, refresh: bool = False) -> pd.DataFrame:
    """優先讀快取；refresh=True 重新下載。"""
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{mk['key']}_{symbol}_{mk['interval']}_{days}d.csv"
    if path.exists() and not refresh:
        df = pd.read_csv(path, index_col="ts")
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    df = fetch(mk, symbol, days)
    df.to_csv(path, index_label="ts")
    return df


# ---------- 模擬資料 ----------
DEMO_PRICE = {"crypto": 60_000, "us": 250, "tw": 1000}

def synthetic(days: int = 60, interval: str = "5m", seed: int = 7, start_price: float = 60_000,
              mk: dict | None = None) -> pd.DataFrame:
    """離線測試用：趨勢／盤整交替、開盤量能放大；股票市場只保留交易時段的平日 K 線。"""
    rng = np.random.default_rng(seed)
    m = INTERVAL_MIN[interval]
    n = days * 1440 // m
    idx = pd.date_range("2026-01-05", periods=n, freq=f"{m}min", tz="UTC")
    tz = (mk or {}).get("tz", "UTC")
    sessions = (mk or {}).get("sessions") or []
    local = idx.tz_convert(tz)
    mod = local.hour * 60 + local.minute
    if sessions:
        keep = (local.weekday < 5) & np.any([(mod >= hhmm(a)) & (mod < hhmm(b)) for a, b in sessions], axis=0)
        opens = [hhmm(a) for a, _ in sessions]
    else:
        keep = np.ones(n, bool)
        opens = [0, 7 * 60, 13 * 60]
    near_open = np.any([(mod >= o) & (mod < o + 60) for o in opens], axis=0)
    per_day = max(int(keep.sum() / days), 1)
    regime = np.repeat(rng.choice([-1, 0, 1], size=n // per_day + 2, p=[.25, .5, .25]), per_day)[:n]
    vol_mult = 1 + 0.8 * near_open
    sigma = 0.0012 * vol_mult
    rets = regime * 0.00012 + sigma * rng.standard_t(4, n) / np.sqrt(2)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.r_[start_price, close[:-1]]
    wick = np.abs(rng.normal(0, sigma, (2, n))) * close
    df = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + wick[0],
                       "low": np.minimum(open_, close) - wick[1], "close": close,
                       "volume": rng.gamma(2, 50, n) * vol_mult * (1 + 20 * np.abs(rets))}, index=idx)
    df.index.name = "ts"
    df["funding"] = np.where((idx.minute == 0) & np.isin(idx.hour, [0, 8, 16]), 0.0001, np.nan) \
        if (mk or {}).get("funding", True) else np.nan
    return df[keep]
