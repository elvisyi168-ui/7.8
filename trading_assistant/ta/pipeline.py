"""一單一流程：掃描訊號 → 出場計畫 → Time Machine → 部位計算 → 風控清單 → PASS / BLOCK 下單單。
只產生下單單，不會自動下單；下單由你本人在券商／交易所完成。
"""
from __future__ import annotations

import csv
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

from . import data, indicators, strategies, timemachine
from .markets import can_enter_now
from .risk import size_position

TRADER_DIR = Path(__file__).resolve().parent.parent / "trader"
JOURNAL = TRADER_DIR / "journal.csv"
TICKETS = TRADER_DIR / "tickets.json"
J_FIELDS = ["date", "market", "symbol", "side", "pnl", "R", "note"]


# ---------- 交易日誌（日虧損上限、每日筆數要用） ----------
def log_trade(market: str, symbol: str, side: str, pnl: float, R: float | None, note: str, date: str) -> None:
    TRADER_DIR.mkdir(exist_ok=True)
    new = not JOURNAL.exists()
    with open(JOURNAL, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, J_FIELDS)
        if new:
            w.writeheader()
        w.writerow({"date": date, "market": market, "symbol": symbol, "side": side, "pnl": pnl,
                    "R": "" if R is None else R, "note": note})


def today_stats(market: str, date: str) -> tuple[float, int]:
    if not JOURNAL.exists():
        return 0.0, 0
    rows = [r for r in csv.DictReader(open(JOURNAL, encoding="utf-8")) if r["market"] == market and r["date"] == date]
    return sum(float(r["pnl"]) for r in rows), len(rows)


# ---------- 下單單 ----------
def build_ticket(symbol: str, df: pd.DataFrame, cfg: dict, equity: float, now: pd.Timestamp,
                 use_journal: bool = True) -> dict | None:
    mk, acc = cfg["market"], cfg["account"]
    d = indicators.add_all(df, cfg)
    sig = strategies.combined(d, cfg)
    last = sig.iloc[-1]
    if last.signal == 0:
        return None
    side = int(last.signal)
    entry = float(d.close.iloc[-1])
    stop, target = float(last.stop), float(last.target)
    bar = pd.Timedelta(minutes=strategies.bar_minutes(d))
    plan = {"side": side, "entry": entry, "stop": stop, "target": target, "strategy": last.strategy,
            "entry_ts": d.index[-1] + bar}
    rr = abs(target - entry) / abs(entry - stop)
    sz = size_position(equity, entry, stop, side, cfg)
    tm = timemachine.run(df.iloc[:-1], cfg, plan)

    date = f"{now.tz_convert(mk['tz']) - pd.Timedelta(hours=mk.get('day_cut_hour', 0)):%Y-%m-%d}"
    day_pnl, day_n = today_stats(mk["key"], date) if use_journal else (0.0, 0)
    open_ok, open_msg = can_enter_now(mk, now)
    max_loss = equity * acc["risk_per_trade_pct"] / 100
    checks = [
        ("交易時段", open_ok, open_msg),
        ("可放空", side > 0 or mk.get("allow_short", False), "做多" if side > 0 else "此市場設定不允許放空"),
        ("停損方向正確", (stop < entry) if side > 0 else (stop > entry), f"停損 {stop:.6g}"),
        ("部位計算", sz.ok, sz.reason or f"數量 {sz.qty:.6g}"),
        ("單筆虧損上限", sz.ok and sz.risk_amount <= max_loss * 1.0001,
         f"打到停損虧 {sz.risk_amount:,.2f} / 上限 {max_loss:,.2f}" if sz.ok else "部位不成立"),
        ("報酬風險比", rr >= acc.get("min_reward_risk", 1.0), f"R:R {rr:.2f} / 最低 {acc.get('min_reward_risk', 1.0)}"),
        ("今日虧損上限", day_pnl > -equity * acc["daily_loss_limit_pct"] / 100,
         f"今日已實現 {day_pnl:+,.2f}（上限 -{equity * acc['daily_loss_limit_pct'] / 100:,.2f}）"),
        ("今日筆數", day_n < acc["max_trades_per_day"], f"{day_n} / {acc['max_trades_per_day']}"),
        ("Time Machine", tm["code"] not in ("negative",), tm["verdict"]),
    ]
    passed = all(ok for _, ok, _ in checks)
    return {"id": f"{mk['key']}-{symbol}-{d.index[-1]:%Y%m%d%H%M}", "market": mk["key"], "market_name": mk["name"],
            "currency": mk.get("currency", ""), "symbol": symbol,
            "signal_bar": f"{d.index[-1].tz_convert(mk['tz']):%Y-%m-%d %H:%M} ({mk['tz']})",
            "strategy": last.strategy, "side": "做多" if side > 0 else "做空", "entry": entry, "stop": stop,
            "target": target, "rr": rr, "adx": float(d.adx.iloc[-1]), "vol_ratio": float(d.vol_ratio.iloc[-1]),
            "flat_by": mk.get("flat_by") or f"最長 {cfg['execution']['max_hold_bars']} 根 K",
            "sizing": sz.__dict__, "timemachine": tm,
            "checks": [{"name": n, "ok": bool(ok), "detail": m} for n, ok, m in checks],
            "status": "PASS" if passed else "BLOCK"}


def format_ticket(t: dict) -> str:
    sz, cur = t["sizing"], t["currency"]
    head = "✅ PASS 可以考慮下單" if t["status"] == "PASS" else "⛔ BLOCK 不要下單"
    lines = [f"══ {head} ══",
             f"{t['market_name']}｜{t['symbol']} {t['side']}｜策略 {t['strategy']}｜訊號 K {t['signal_bar']}",
             f"進場≈{t['entry']:.6g}  停損 {t['stop']:.6g}  停利 {t['target']:.6g}  R:R {t['rr']:.2f}  "
             f"出場期限：{t['flat_by']}"]
    if sz["ok"]:
        extra = f"｜強平≈{sz['liq_price']:.6g}" if sz["liq_price"] else ""
        lines.append(f"數量 {sz['qty']:.6g}｜名目 {sz['notional']:,.2f} {cur}｜保證金 {sz['margin']:,.2f} {cur}"
                     f"｜打到停損最多虧 {sz['risk_amount']:,.2f} {cur}（含成本 {sz['fees']:,.2f}）{extra}")
    lines.append(timemachine.format_report(t["timemachine"]))
    lines.append("風控清單：")
    lines += [f"  {'✔' if c['ok'] else '✘'} {c['name']}：{c['detail']}" for c in t["checks"]]
    return "\n".join(lines)


def save_tickets(tickets: list[dict]) -> None:
    TRADER_DIR.mkdir(exist_ok=True)
    TICKETS.write_text(json.dumps(tickets, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def notify_telegram(text: str) -> None:
    token, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        print("（未設定 TG_TOKEN / TG_CHAT_ID，略過推播）")
        return
    body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=10)


def run_market(cfg: dict, equity: float | None = None, demo: bool = False, lookback_days: int = 60) -> list[dict]:
    mk = cfg["market"]
    equity = equity or cfg["account"]["equity"]
    out = []
    for i, sym in enumerate(mk["symbols"]):
        try:
            if demo:
                df = data.synthetic(lookback_days, mk["interval"], seed=11 + i,
                                    start_price=data.DEMO_PRICE.get(mk["key"], 100), mk=mk)
                sig = strategies.combined(indicators.add_all(df, cfg), cfg)
                hits = sig.index[sig.signal != 0]
                if len(hits) == 0:
                    continue
                df = df.loc[:hits[-1]]  # 模擬「剛好收到訊號」那一刻
                now = df.index[-1] + pd.Timedelta(minutes=strategies.bar_minutes(df))
            else:
                df = data.fetch(mk, sym, lookback_days)
                now = pd.Timestamp.now(tz="UTC")
            t = build_ticket(sym, df, cfg, equity, now, use_journal=not demo)
        except Exception as e:  # 單一商品失敗不影響其他
            print(f"{mk['name']} {sym} 失敗：{e}")
            continue
        if t:
            out.append(t)
            msg = format_ticket(t)
            print(msg + "\n")
            if cfg["notify"].get("telegram") and t["status"] == "PASS":
                notify_telegram(msg)
    if not out:
        print(f"{mk['name']}：{pd.Timestamp.now(tz=mk['tz']):%H:%M} 無訊號")
    return out


def loop(cfgs: list[dict]) -> None:
    step = min(data.INTERVAL_MIN[c["market"]["interval"]] for c in cfgs) * 60
    print(f"每 {step // 60} 分鐘掃描一次，Ctrl+C 結束")
    while True:
        tickets = []
        for c in cfgs:
            ok, _ = can_enter_now(c["market"], pd.Timestamp.now(tz="UTC"))
            if ok:
                tickets += run_market(c)
        save_tickets(tickets)
        time.sleep(step - time.time() % step + 5)  # 對齊下一根 K 收盤後 5 秒
