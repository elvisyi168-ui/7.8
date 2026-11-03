"""防過度擬合：參數敏感度掃描 + Walk-forward（滾動樣本外驗證）。"""
from __future__ import annotations

import copy
import itertools

import pandas as pd

from . import backtest, metrics

# 預設掃描範圍：只掃少數關鍵參數，掃太多＝自我欺騙
DEFAULT_GRID = {
    "orb.atr_stop_mult": [1.0, 1.5, 2.0],
    "orb.reward_risk": [1.5, 2.0, 3.0],
    "orb.min_volume_ratio": [1.2, 1.5, 2.0],
}


def _apply(cfg: dict, params: dict) -> dict:
    c = copy.deepcopy(cfg)
    for k, v in params.items():
        strat, key = k.split(".")
        c["strategy"][strat][key] = v
    return c


def _score(s: dict) -> float:
    # 以「報酬/回撤」排序，交易數太少直接淘汰
    return s.get("報酬/回撤", -99) if s.get("交易次數", 0) >= 10 else -99


def grid_search(df: pd.DataFrame, cfg: dict, grid: dict | None = None) -> pd.DataFrame:
    grid = grid or DEFAULT_GRID
    keys = list(grid)
    rows = []
    for combo in itertools.product(*grid.values()):
        params = dict(zip(keys, combo))
        s = metrics.summary(backtest.run(df, _apply(cfg, params)))
        rows.append({**params, **{k: s.get(k) for k in ("總報酬%", "最大回撤%", "勝率%", "期望值(R)", "交易次數", "報酬/回撤")},
                     "score": _score(s)})
    return pd.DataFrame(rows).sort_values("score", ascending=False)


def walk_forward(df: pd.DataFrame, cfg: dict, folds: int = 4, train_ratio: float = 0.7,
                 grid: dict | None = None) -> pd.DataFrame:
    """把資料切成 folds 段；每段前 70% 找最佳參數、後 30% 用該參數測試（沒看過的資料）。"""
    n = len(df)
    seg = n // folds
    rows = []
    for k in range(folds):
        part = df.iloc[k * seg:(k + 1) * seg]
        cut = int(len(part) * train_ratio)
        train, test = part.iloc[:cut], part.iloc[cut:]
        best = grid_search(train, cfg, grid).iloc[0]
        params = {p: best[p] for p in (grid or DEFAULT_GRID)}
        s_in = metrics.summary(backtest.run(train, _apply(cfg, params)))
        s_out = metrics.summary(backtest.run(test, _apply(cfg, params)))
        rows.append({"段": k + 1, "測試期": f"{test.index[0]:%m/%d}~{test.index[-1]:%m/%d}", **params,
                     "樣本內報酬%": s_in["總報酬%"], "樣本外報酬%": s_out["總報酬%"],
                     "樣本外回撤%": s_out["最大回撤%"], "樣本外交易數": s_out["交易次數"]})
    return pd.DataFrame(rows)
