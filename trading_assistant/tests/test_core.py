"""核心規則測試：python3 -m unittest discover -s tests"""
import copy
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ta import backtest, config, data, indicators, markets, metrics, pipeline, strategies, timemachine  # noqa: E402
from ta.risk import liquidation_price, size_position  # noqa: E402

RAW = config.load_raw(ROOT / "config.toml")
CRYPTO, US, TW = (config.market_cfg(RAW, k) for k in ("crypto", "us", "tw"))


def no_cost(cfg):
    c = copy.deepcopy(cfg)
    c["costs"].update(fee_pct=0, maker_fee_pct=0, slippage_bps=0, min_fee=0, sell_tax_pct=0)
    c["account"]["max_fee_r"] = 1.0
    return c


def bars(rows, start="2026-01-01 01:00"):
    idx = pd.date_range(start, periods=len(rows), freq="5min", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = 100.0
    df["funding"] = np.nan
    return df


def one_signal(df, side, stop, target, at=0):
    s = pd.DataFrame({"signal": 0, "stop": np.nan, "target": np.nan, "strategy": ""}, index=df.index)
    s.iloc[at] = [side, stop, target, "test"]
    return s


class TestConfig(unittest.TestCase):
    def test_market_overrides_defaults(self):
        self.assertEqual(CRYPTO["account"]["max_leverage"], 10)
        self.assertEqual(US["account"]["max_leverage"], 1)
        self.assertEqual(TW["costs"]["min_fee"], 20)
        self.assertEqual(US["strategy"]["orb"]["session_times"], ["09:30"])
        self.assertEqual(US["strategy"]["vwap"]["max_adx"], 20)  # 沒覆蓋的沿用預設


class TestCosts(unittest.TestCase):
    def test_tw_min_fee_and_tax(self):
        c = TW["costs"]
        self.assertEqual(markets.fee(c, 100, 1, is_sell=False), 20)            # 最低 20 元
        self.assertAlmostEqual(markets.fee(c, 1000, 1000, is_sell=True),
                               1000 * 1000 * (0.0855 + 0.15) / 100)             # 手續費 + 當沖稅

    def test_last_entry_blocks(self):
        cfg = no_cost(US)
        idx = pd.date_range("2026-03-02 15:00", periods=3, freq="5min", tz="America/New_York").tz_convert("UTC")
        df = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1.0,
                           "funding": np.nan}, index=idx)
        self.assertTrue(backtest.run(df, cfg, one_signal(df, 1, 95, 110)).trades.empty)

    def test_tw_small_odd_lot_rejected_by_cost(self):
        sz = size_position(10_000, 100, 99, 1, TW)  # 風險 100 元，最低手續費就吃掉一大半
        self.assertFalse(sz.ok)


class TestRisk(unittest.TestCase):
    def test_risk_amount_respected(self):
        sz = size_position(1000, 100, 99, 1, CRYPTO)
        self.assertTrue(sz.ok)
        self.assertLessEqual(sz.risk_amount, 10.0 + 1e-9)

    def test_whole_shares_and_cash_cap(self):
        sz = size_position(5000, 250, 249, 1, US)
        self.assertTrue(sz.ok)
        self.assertEqual(sz.qty, int(sz.qty))
        self.assertLessEqual(sz.notional, 5000)

    def test_liq_closer_than_stop_rejected(self):
        self.assertFalse(size_position(1000, 100, 80, 1, CRYPTO).ok)

    def test_fee_filter(self):
        self.assertFalse(size_position(1000, 100, 99.97, 1, CRYPTO, leverage=100).ok)

    def test_liq_formula(self):
        self.assertAlmostEqual(liquidation_price(100, 1, 10, 0), 90)
        self.assertAlmostEqual(liquidation_price(100, -1, 10, 0), 110)


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.cfg = no_cost(CRYPTO)

    def test_next_open_entry_and_target(self):
        df = bars([[100, 100, 100, 100], [101, 101, 101, 101], [101, 105, 101, 104]])
        t = backtest.run(df, self.cfg, one_signal(df, 1, 99, 104)).trades.iloc[0]
        self.assertEqual(t.entry, 101)
        self.assertEqual(t.reason, "停利")
        self.assertAlmostEqual(t.R, 1.5, places=6)

    def test_stop_first_when_both_hit(self):
        df = bars([[100, 100, 100, 100], [100, 100, 100, 100], [100, 110, 90, 100]])
        self.assertEqual(backtest.run(df, self.cfg, one_signal(df, 1, 98, 104)).trades.iloc[0].reason, "停損")

    def test_gap_through_stop_fills_at_open(self):
        df = bars([[100, 100, 100, 100], [100, 100, 100, 100], [95, 96, 94, 95]])
        self.assertEqual(backtest.run(df, self.cfg, one_signal(df, 1, 98, 104)).trades.iloc[0].exit, 95)

    def test_short_side(self):
        df = bars([[100, 100, 100, 100], [100, 100, 100, 100], [100, 100, 96, 97]])
        self.assertGreater(backtest.run(df, self.cfg, one_signal(df, -1, 102, 97)).trades.iloc[0].pnl, 0)

    def test_us_flat_by_close(self):
        """美股 15:55（紐約）前要平倉。"""
        cfg = no_cost(US)
        cfg["market"]["last_entry"] = ""
        idx = pd.date_range("2026-03-02 15:40", periods=4, freq="5min", tz="America/New_York").tz_convert("UTC")
        df = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1.0,
                           "funding": np.nan}, index=idx)
        t = backtest.run(df, cfg, one_signal(df, 1, 95, 110)).trades.iloc[0]
        self.assertEqual(t.reason, "收盤前平倉")
        self.assertEqual(t.exit_ts.tz_convert("America/New_York").strftime("%H:%M"), "15:55")

    def test_accounting_matches_all_markets(self):
        for cfg, px in ((CRYPTO, 60000), (US, 250), (TW, 1000)):
            r = backtest.run(data.synthetic(20, start_price=px, mk=cfg["market"]), cfg)
            if r.trades.empty:  # 台股成本高，短停損會全被成本過濾擋下，屬正常
                continue
            self.assertAlmostEqual(r.equity.iloc[-1] - cfg["account"]["equity"], r.trades.pnl.sum(), places=4)

    def test_no_lookahead(self):
        for cfg in (CRYPTO, US):
            df = data.synthetic(10, mk=cfg["market"])
            full = strategies.combined(indicators.add_all(df, cfg), cfg)
            cut = len(df) - 100
            part = strategies.combined(indicators.add_all(df.iloc[:cut], cfg), cfg)
            pd.testing.assert_series_equal(full.signal.iloc[:cut], part.signal, check_names=False)

    def test_no_short_when_disallowed(self):
        df = indicators.add_all(data.synthetic(30, mk=US["market"], start_price=250), US)
        self.assertFalse((strategies.combined(df, US).signal < 0).any())

    def test_orb_anchor_local_time(self):
        """美股開盤區間要從紐約 09:30 起算（含夏令時間）。"""
        df = indicators.add_all(data.synthetic(40, mk=US["market"], start_price=250), US)
        s = strategies.orb(df, US["strategy"]["orb"], US["market"])
        first = s.index[s.signal != 0].tz_convert("America/New_York")
        mins = first.hour * 60 + first.minute
        self.assertTrue(((mins >= 9 * 60 + 45) & (mins < 9 * 60 + 45 + 90)).all())

    def test_stop_modes(self):
        df = indicators.add_all(data.synthetic(30, mk=US["market"], start_price=250), US)
        for mode in ("atr", "or_mid", "or_low"):
            p = {**US["strategy"]["orb"], "stop_mode": mode}
            s = strategies.orb(df, p, US["market"])
            L = s[s.signal > 0]
            self.assertTrue((L.stop < df.close[L.index]).all(), mode)
            if mode == "or_mid":
                self.assertTrue(np.allclose(L.stop, (L.or_high + L.or_low) / 2))


class TestTimeMachine(unittest.TestCase):
    def test_probabilities_and_reproducible(self):
        hist = data.synthetic(30, mk=US["market"], start_price=250)
        ts = pd.Timestamp("2026-03-02 10:00", tz="America/New_York").tz_convert("UTC")
        a = timemachine.simulate(hist, US, 1, 250, 248, 254, ts, runs=2000, seed=1)
        b = timemachine.simulate(hist, US, 1, 250, 248, 254, ts, runs=2000, seed=1)
        self.assertTrue(a["ok"])
        self.assertAlmostEqual(a["p_target"] + a["p_stop"] + a["p_open"], 1.0)
        self.assertEqual(a, b)

    def test_tight_target_mostly_wins(self):
        hist = data.synthetic(30, mk=CRYPTO["market"])
        ts = hist.index[-1]
        p = hist.close.iloc[-1]
        s = timemachine.simulate(hist, CRYPTO, 1, p, p * 0.95, p * 1.0005, ts, runs=2000)
        self.assertGreater(s["p_target"], 0.8)

    def test_verdict(self):
        self.assertEqual(timemachine.verdict({"ok": True, "mean_R": 0.3}, {"n": 20, "mean_R": 0.2})[1], "edge")
        self.assertEqual(timemachine.verdict({"ok": True, "mean_R": -0.3}, {"n": 0})[1], "negative")
        self.assertEqual(timemachine.verdict({"ok": True, "mean_R": 0.05}, {"n": 0})[1], "coinflip")


class TestPipeline(unittest.TestCase):
    def test_demo_tickets_have_checks(self):
        for cfg in (CRYPTO, US, TW):
            for t in pipeline.run_market(cfg, demo=True, lookback_days=30):
                self.assertIn(t["status"], ("PASS", "BLOCK"))
                names = {c["name"] for c in t["checks"]}
                self.assertTrue({"單筆虧損上限", "Time Machine", "交易時段"} <= names)
                if t["status"] == "PASS":
                    self.assertTrue(all(c["ok"] for c in t["checks"]))


class TestData(unittest.TestCase):
    def test_parse_yahoo(self):
        j = {"chart": {"result": [{"timestamp": [1772461800, 1772462100],
                                   "indicators": {"quote": [{"open": [1, None], "high": [2, None], "low": [0.5, None],
                                                             "close": [1.5, None], "volume": [10, None]}]}}]}}
        df = data.parse_yahoo(j)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.close.iloc[0], 1.5)

    def test_summary_runs(self):
        self.assertIn("最大回撤%", metrics.summary(backtest.run(data.synthetic(10), CRYPTO)))


if __name__ == "__main__":
    unittest.main()
