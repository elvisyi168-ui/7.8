"""交易幫手指令入口。用法見 README.md。"""
from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

import pandas as pd

from ta import backtest, data, metrics, report, scanner, validate
from ta.risk import size_position

ROOT = Path(__file__).resolve().parent
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)


def load_cfg(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def get_df(args, cfg, symbol):
    if args.demo:
        return data.synthetic(args.days, cfg["market"]["interval"])
    return data.load(symbol, cfg["market"]["interval"], args.days, args.refresh)


def cmd_backtest(args, cfg):
    symbols = [args.symbol] if args.symbol else cfg["market"]["symbols"][:1 if args.demo else None]
    for sym in symbols:
        name = "DEMO" if args.demo else sym
        res = backtest.run(get_df(args, cfg, sym), cfg)
        print(f"\n===== {name} {cfg['market']['interval']} {args.days} 天 =====")
        for k, v in metrics.summary(res).items():
            print(f"  {k:<10} {v}")
        print("\n各策略：\n", metrics.breakdown(res.trades, "strategy"))
        out = report.build(res, ROOT / "reports" / f"{name}_{cfg['market']['interval']}.html", f"{name} 回測報告")
        print(f"\n報告：{out}")


def cmd_optimize(args, cfg):
    sym = args.symbol or cfg["market"]["symbols"][0]
    g = validate.grid_search(get_df(args, cfg, sym), cfg)
    print("參數敏感度（前 15 名）：好策略應該是「一片高原」而不是「一根尖刺」")
    print(g.head(15).to_string(index=False))


def cmd_wf(args, cfg):
    sym = args.symbol or cfg["market"]["symbols"][0]
    w = validate.walk_forward(get_df(args, cfg, sym), cfg, folds=args.folds)
    print(w.to_string(index=False))
    pos = (w["樣本外報酬%"] > 0).sum()
    print(f"\n樣本外獲利段數：{pos}/{len(w)}（建議至少過半才考慮實盤）")


def cmd_scan(args, cfg):
    eq = args.equity or cfg["account"]["equity"]
    scanner.loop(cfg, eq) if args.loop else scanner.scan_once(cfg, eq)


def cmd_size(args, cfg):
    acc = cfg["account"]
    side = 1 if args.stop < args.entry else -1
    sz = size_position(args.equity or acc["equity"], args.entry, args.stop, side,
                       args.risk or acc["risk_per_trade_pct"], args.lev or acc["max_leverage"],
                       acc["maintenance_margin_rate"], cfg["costs"]["taker_fee_pct"], acc.get("max_fee_r", 1.0))
    print(f"方向：{'做多' if side > 0 else '做空'}")
    if not sz.ok:
        print("⚠️", sz.reason)
        return
    print(f"數量 {sz.qty:.6g}｜名目 {sz.notional:,.2f} U｜保證金 {sz.margin:,.2f} U｜實際曝險 {sz.leverage_used:.2f}x")
    print(f"強平價≈{sz.liq_price:.6g}｜打到停損最多虧 {sz.risk_amount:,.2f} U（含手續費）")


def main(argv=None):
    ap = argparse.ArgumentParser(description="加密永續合約當沖交易幫手")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--symbol")
        p.add_argument("--days", type=int, default=90)
        p.add_argument("--demo", action="store_true", help="用模擬資料（離線測試）")
        p.add_argument("--refresh", action="store_true", help="重新下載資料")

    common(sub.add_parser("backtest", help="回測並產出 HTML 報告"))
    common(sub.add_parser("optimize", help="參數敏感度掃描"))
    p = sub.add_parser("wf", help="Walk-forward 樣本外驗證")
    common(p)
    p.add_argument("--folds", type=int, default=4)
    p = sub.add_parser("scan", help="即時掃描訊號")
    p.add_argument("--loop", action="store_true", help="每根 K 收盤自動掃描")
    p.add_argument("--equity", type=float)
    p = sub.add_parser("size", help="部位計算機")
    p.add_argument("entry", type=float)
    p.add_argument("stop", type=float)
    p.add_argument("--equity", type=float)
    p.add_argument("--risk", type=float, help="單筆風險 %%")
    p.add_argument("--lev", type=float, help="槓桿")

    args = ap.parse_args(argv)
    cfg = load_cfg(args.config)
    {"backtest": cmd_backtest, "optimize": cmd_optimize, "wf": cmd_wf,
     "scan": cmd_scan, "size": cmd_size}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
