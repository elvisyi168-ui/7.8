"""即時掃描：抓最新已收盤 K 線，若出現訊號就給出完整下單計畫（不會自動下單）。"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

import pandas as pd

from . import data, indicators, strategies
from .risk import size_position


def plan_for(symbol: str, df: pd.DataFrame, cfg: dict, equity: float) -> dict | None:
    d = indicators.add_all(df, cfg)
    sig = strategies.combined(d, cfg)
    last = sig.iloc[-1]
    if last.signal == 0:
        return None
    side = int(last.signal)
    entry = float(d.close.iloc[-1])
    acc = cfg["account"]
    sz = size_position(equity, entry, last.stop, side, acc["risk_per_trade_pct"], acc["max_leverage"],
                       acc["maintenance_margin_rate"], cfg["costs"]["taker_fee_pct"], acc.get("max_fee_r", 1.0))
    return {"symbol": symbol, "time": f"{d.index[-1]:%Y-%m-%d %H:%M} UTC", "strategy": last.strategy,
            "side": "做多" if side > 0 else "做空", "entry": entry, "stop": float(last.stop),
            "target": float(last.target), "rr": abs(last.target - entry) / abs(entry - last.stop),
            "adx": float(d.adx.iloc[-1]), "vol_ratio": float(d.vol_ratio.iloc[-1]), "sizing": sz}


def format_plan(p: dict) -> str:
    sz = p["sizing"]
    lines = [f"🔔 {p['symbol']} {p['side']}｜策略 {p['strategy']}｜{p['time']}",
             f"進場≈{p['entry']:.6g}  停損 {p['stop']:.6g}  停利 {p['target']:.6g}  (R:R {p['rr']:.2f})",
             f"ADX {p['adx']:.1f}  量比 {p['vol_ratio']:.2f}"]
    if sz.ok:
        lines.append(f"數量 {sz.qty:.6g}｜名目 {sz.notional:,.2f} U｜保證金 {sz.margin:,.2f} U"
                     f"｜實際曝險 {sz.leverage_used:.1f}x｜強平≈{sz.liq_price:.6g}｜最多虧 {sz.risk_amount:,.2f} U")
    else:
        lines.append(f"⚠️ 不建議下單：{sz.reason}")
    return "\n".join(lines)


def notify_telegram(text: str) -> None:
    token, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        print("（未設定 TG_TOKEN / TG_CHAT_ID，略過推播）")
        return
    body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=10)


def scan_once(cfg: dict, equity: float, lookback_days: int = 3) -> list[dict]:
    plans = []
    for sym in cfg["market"]["symbols"]:
        try:
            df = data.fetch_klines(sym, cfg["market"]["interval"], lookback_days)
            p = plan_for(sym, df, cfg, equity)
        except Exception as e:  # 單一幣種失敗不影響其他
            print(f"{sym} 讀取失敗：{e}")
            continue
        if p:
            plans.append(p)
            msg = format_plan(p)
            print(msg + "\n")
            if cfg["notify"].get("telegram"):
                notify_telegram(msg)
    if not plans:
        print(f"{pd.Timestamp.now(tz='UTC'):%H:%M} UTC 無訊號")
    return plans


def loop(cfg: dict, equity: float) -> None:
    step = data.INTERVAL_MIN[cfg["market"]["interval"]] * 60
    print(f"每 {step // 60} 分鐘掃描一次，Ctrl+C 結束")
    while True:
        scan_once(cfg, equity)
        time.sleep(step - time.time() % step + 3)  # 對齊下一根 K 收盤後 3 秒
