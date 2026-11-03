"""多市場當沖交易幫手（台股／美股／加密永續合約）。用法見 README.md。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from ta import backtest, config, data, indicators, metrics, pipeline, report, strategies, timemachine, validate
from ta.risk import size_position

ROOT = Path(__file__).resolve().parent
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)


def get_df(args, cfg, symbol, seed=7):
    mk = cfg["market"]
    if args.demo:
        return data.synthetic(args.days, mk["interval"], seed=seed, start_price=data.DEMO_PRICE.get(mk["key"], 100), mk=mk)
    return data.load(mk, symbol, args.days, args.refresh)


def markets_of(args, raw):
    return list(raw["markets"]) if args.market == "all" else args.market.split(",")


def cmd_backtest(args, raw):
    for key in markets_of(args, raw):
        cfg = config.market_cfg(raw, key)
        mk = cfg["market"]
        symbols = [args.symbol] if args.symbol else mk["symbols"][:1 if args.demo else None]
        for sym in symbols:
            name = f"{key}-DEMO" if args.demo else f"{key}-{sym}"
            res = backtest.run(get_df(args, cfg, sym), cfg)
            print(f"\n===== {mk['name']} {name} {mk['interval']} {args.days} 天（{mk.get('currency')}） =====")
            for k, v in metrics.summary(res).items():
                print(f"  {k:<10} {v}")
            print("\n各策略：\n", metrics.breakdown(res.trades, "strategy"))
            out = report.build(res, ROOT / "reports" / f"{name}_{mk['interval']}.html", f"{mk['name']} {name} 回測報告")
            print(f"報告：{out}")


def _one(args, raw):
    key = markets_of(args, raw)[0]
    cfg = config.market_cfg(raw, key)
    return cfg, args.symbol or cfg["market"]["symbols"][0]


def cmd_optimize(args, raw):
    cfg, sym = _one(args, raw)
    g = validate.grid_search(get_df(args, cfg, sym), cfg)
    print(f"{cfg['market']['name']} {sym} 參數敏感度（前 15 名）：好策略是「一片高原」不是「一根尖刺」")
    print(g.head(15).to_string(index=False))
    print("\n停損模式比較（各模式最佳一組）：")
    print(g.sort_values("score", ascending=False).groupby("orb.stop_mode").head(1).to_string(index=False))


def cmd_wf(args, raw):
    cfg, sym = _one(args, raw)
    w = validate.walk_forward(get_df(args, cfg, sym), cfg, folds=args.folds)
    print(w.to_string(index=False))
    print(f"\n樣本外獲利段數：{(w['樣本外報酬%'] > 0).sum()}/{len(w)}（建議至少過半才考慮實盤）")


def cmd_plan(args, raw):
    cfgs = [config.market_cfg(raw, k) for k in markets_of(args, raw)]
    if args.loop:
        return pipeline.loop(cfgs)
    tickets = []
    for c in cfgs:
        tickets += pipeline.run_market(c, demo=args.demo)
    pipeline.save_tickets(tickets)
    p = sum(t["status"] == "PASS" for t in tickets)
    print(f"共 {len(tickets)} 張下單單，PASS {p}、BLOCK {len(tickets) - p} → {pipeline.TICKETS}")


def cmd_tm(args, raw):
    cfg, sym = _one(args, raw)
    df = get_df(args, cfg, sym) if args.demo else data.fetch(cfg["market"], sym, 60)
    if args.entry and args.stop and args.target:
        side = 1 if args.stop < args.entry else -1
        bar = pd.Timedelta(minutes=strategies.bar_minutes(df))
        plan = {"side": side, "entry": args.entry, "stop": args.stop, "target": args.target,
                "strategy": args.strategy, "entry_ts": df.index[-1] + bar}
        if args.at:
            local = df.index[-1].tz_convert(cfg["market"]["tz"])
            hh, mm = map(int, args.at.split(":"))
            plan["entry_ts"] = local.replace(hour=hh, minute=mm).tz_convert("UTC")
        hist = df
    else:  # 用最近一次訊號
        sig = strategies.combined(indicators.add_all(df, cfg), cfg)
        hits = sig.index[sig.signal != 0]
        if len(hits) == 0:
            return print("資料期間內沒有訊號；請用 --entry --stop --target 手動指定")
        t0 = hits[-1]
        r = sig.loc[t0]
        hist = df.loc[:t0]
        bar = pd.Timedelta(minutes=strategies.bar_minutes(df))
        plan = {"side": int(r.signal), "entry": float(df.close.loc[t0]), "stop": float(r.stop),
                "target": float(r.target), "strategy": r.strategy, "entry_ts": t0 + bar}
        print(f"最近訊號：{t0.tz_convert(cfg['market']['tz']):%Y-%m-%d %H:%M} {r.strategy} "
              f"{'多' if r.signal > 0 else '空'} 進場≈{plan['entry']:.6g} 停損 {plan['stop']:.6g} 停利 {plan['target']:.6g}")
    print(timemachine.format_report(timemachine.run(hist.iloc[:-1] if hist is not df else hist, cfg, plan)))


def cmd_size(args, raw):
    cfg, _ = _one(args, raw)
    side = 1 if args.stop < args.entry else -1
    sz = size_position(args.equity or cfg["account"]["equity"], args.entry, args.stop, side, cfg,
                       risk_pct=args.risk, leverage=args.lev)
    cur = cfg["market"].get("currency", "")
    print(f"{cfg['market']['name']}｜方向：{'做多' if side > 0 else '做空'}")
    if not sz.ok:
        return print("⚠️", sz.reason)
    liq = f"｜強平價≈{sz.liq_price:.6g}" if sz.liq_price else ""
    print(f"數量 {sz.qty:.6g}｜名目 {sz.notional:,.2f} {cur}｜保證金 {sz.margin:,.2f} {cur}｜實際曝險 {sz.leverage_used:.2f}x{liq}")
    print(f"打到停損最多虧 {sz.risk_amount:,.2f} {cur}（含來回成本 {sz.fees:,.2f}）")


def cmd_log(args, raw):
    cfg, _ = _one(args, raw)
    mk = cfg["market"]
    date = args.date or f"{pd.Timestamp.now(tz=mk['tz']) - pd.Timedelta(hours=mk.get('day_cut_hour', 0)):%Y-%m-%d}"
    pipeline.log_trade(mk["key"], args.symbol, args.side, args.pnl, args.R, args.note or "", date)
    pnl, n = pipeline.today_stats(mk["key"], date)
    print(f"已記錄。{mk['name']} {date}：{n} 筆，已實現 {pnl:+,.2f} {mk.get('currency', '')}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="多市場當沖交易幫手（台股／美股／加密）")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, market_default="crypto"):
        p.add_argument("--market", default=market_default, help="crypto / us / tw / all，可用逗號串接")
        p.add_argument("--symbol")
        p.add_argument("--days", type=int, default=60)
        p.add_argument("--demo", action="store_true", help="用模擬資料（離線測試）")
        p.add_argument("--refresh", action="store_true", help="重新下載資料")
        return p

    common(sub.add_parser("backtest", help="回測並產出 HTML 報告"))
    common(sub.add_parser("optimize", help="參數敏感度 + 停損模式比較"))
    common(sub.add_parser("wf", help="Walk-forward 樣本外驗證")).add_argument("--folds", type=int, default=4)
    p = common(sub.add_parser("plan", help="掃描 → Time Machine → 部位 → 風控 → PASS/BLOCK 下單單"), "all")
    p.add_argument("--loop", action="store_true", help="每根 K 收盤自動跑")
    p = common(sub.add_parser("tm", help="Time Machine：這一單的誠實機率"))
    for a in ("entry", "stop", "target"):
        p.add_argument(f"--{a}", type=float)
    p.add_argument("--strategy", default="orb", help="歷史重播用哪個策略")
    p.add_argument("--at", help="進場時間（當地 HH:MM），預設最新一根")
    p = common(sub.add_parser("size", help="部位計算機"))
    p.add_argument("entry", type=float)
    p.add_argument("stop", type=float)
    p.add_argument("--equity", type=float)
    p.add_argument("--risk", type=float, help="單筆風險 %%")
    p.add_argument("--lev", type=float, help="槓桿")
    p = common(sub.add_parser("log", help="記錄已平倉交易（日虧損上限、每日筆數用）"))
    p.add_argument("--pnl", type=float, required=True)
    p.add_argument("--side", default="多")
    p.add_argument("--R", type=float)
    p.add_argument("--note")
    p.add_argument("--date")

    args = ap.parse_args(argv)
    raw = config.load_raw(args.config)
    {"backtest": cmd_backtest, "optimize": cmd_optimize, "wf": cmd_wf, "plan": cmd_plan,
     "tm": cmd_tm, "size": cmd_size, "log": cmd_log}[args.cmd](args, raw)


if __name__ == "__main__":
    sys.exit(main())
