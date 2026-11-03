"""讀設定檔並合併：defaults + markets.<key> → 單一市場的完整設定。"""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path

SECTIONS = {"account", "costs", "execution", "strategy", "indicators", "timemachine", "notify"}


def _merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def load_raw(path: str | Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def market_cfg(raw: dict, key: str) -> dict:
    if key not in raw["markets"]:
        raise SystemExit(f"未知市場 {key}，可用：{', '.join(raw['markets'])}")
    m = raw["markets"][key]
    cfg = _merge(raw.get("defaults", {}), {k: v for k, v in m.items() if k in SECTIONS})
    cfg["market"] = {"key": key, **{k: v for k, v in m.items() if k not in SECTIONS}}
    if cfg["costs"].get("maker_fee_pct", -1) < 0:
        cfg["costs"]["maker_fee_pct"] = cfg["costs"]["fee_pct"]
    return cfg
