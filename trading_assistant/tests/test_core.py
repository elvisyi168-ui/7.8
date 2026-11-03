"""核心規則測試：python3 -m unittest discover -s tests"""
import sys
import tomllib
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ta import backtest, data, metrics  # noqa: E402
from ta.risk import liquidation_price, size_position  # noqa: E402

CFG = tomllib.load(open(ROOT / "config.toml", "rb"))


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


class TestRisk(unittest.TestCase):
    def test_risk_amount_respected(self):
        sz = size_position(1000, 100, 99, 1, 1.0, 20, 0.005, 0.05)
        self.assertTrue(sz.ok)
        self.assertAlmostEqual(sz.risk_amount, 10.0, places=6)

    def test_leverage_cap(self):
        sz = size_position(1000, 100, 99.99, 1, 1.0, 5, 0.005, 0.0)
        self.assertLessEqual(sz.notional, 5000 + 1e-6)

    def test_liq_closer_than_stop_rejected(self):
        sz = size_position(1000, 100, 80, 1, 1.0, 10, 0.005)  # 10x 強平約 -9.5%，停損 -20%
        self.assertFalse(sz.ok)

    def test_fee_filter(self):
        sz = size_position(1000, 100, 99.97, 1, 1.0, 100, 0.005, 0.05, max_fee_r=0.15)
        self.assertFalse(sz.ok)

    def test_liq_formula(self):
        self.assertAlmostEqual(liquidation_price(100, 1, 10, 0), 90)
        self.assertAlmostEqual(liquidation_price(100, -1, 10, 0), 110)


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.cfg = {**CFG, "costs": {**CFG["costs"], "taker_fee_pct": 0, "maker_fee_pct": 0, "slippage_bps": 0},
                    "account": {**CFG["account"], "max_fee_r": 1.0}}

    def test_next_open_entry_and_target(self):
        df = bars([[100, 100, 100, 100], [101, 101, 101, 101], [101, 105, 101, 104]])
        r = backtest.run(df, self.cfg, one_signal(df, 1, 99, 104))
        t = r.trades.iloc[0]
        self.assertEqual(t.entry, 101)          # 下一根開盤進場
        self.assertEqual(t.reason, "停利")
        self.assertAlmostEqual(t.R, 1.5, places=6)  # (104-101)/(101-99)

    def test_stop_first_when_both_hit(self):
        df = bars([[100, 100, 100, 100], [100, 100, 100, 100], [100, 110, 90, 100]])
        r = backtest.run(df, self.cfg, one_signal(df, 1, 98, 104))
        self.assertEqual(r.trades.iloc[0].reason, "停損")

    def test_gap_through_stop_fills_at_open(self):
        df = bars([[100, 100, 100, 100], [100, 100, 100, 100], [95, 96, 94, 95]])
        r = backtest.run(df, self.cfg, one_signal(df, 1, 98, 104))
        self.assertEqual(r.trades.iloc[0].exit, 95)

    def test_short_side(self):
        df = bars([[100, 100, 100, 100], [100, 100, 100, 100], [100, 100, 96, 97]])
        r = backtest.run(df, self.cfg, one_signal(df, -1, 102, 97))
        self.assertGreater(r.trades.iloc[0].pnl, 0)

    def test_accounting_matches_with_costs(self):
        r = backtest.run(data.synthetic(20), CFG)
        self.assertAlmostEqual(r.equity.iloc[-1] - CFG["account"]["equity"], r.trades.pnl.sum(), places=4)

    def test_no_lookahead(self):
        """截斷未來資料後，過去的訊號不應改變。"""
        from ta import indicators, strategies
        df = data.synthetic(10)
        full = strategies.combined(indicators.add_all(df, CFG), CFG)
        cut = len(df) - 300
        part = strategies.combined(indicators.add_all(df.iloc[:cut], CFG), CFG)
        pd.testing.assert_series_equal(full.signal.iloc[:cut], part.signal, check_names=False)

    def test_summary_runs(self):
        s = metrics.summary(backtest.run(data.synthetic(10), CFG))
        self.assertIn("最大回撤%", s)


if __name__ == "__main__":
    unittest.main()
