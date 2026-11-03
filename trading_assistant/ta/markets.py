"""市場規則：交易時段、成本計算、目前能否進場。"""
from __future__ import annotations

import pandas as pd


def hhmm(s: str) -> int | None:
    if not s:
        return None
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fee(costs: dict, price: float, qty: float, is_sell: bool, maker: bool = False) -> float:
    """單邊成本：手續費（含最低收費）+ 賣出稅費。"""
    if qty <= 0:
        return 0.0
    rate = costs["maker_fee_pct"] if maker else costs["fee_pct"]
    f = max(price * qty * rate / 100, costs.get("min_fee", 0.0))
    if is_sell:
        f += price * qty * costs.get("sell_tax_pct", 0.0) / 100
    return f


def roundtrip_rate(costs: dict) -> float:
    """來回成本比例（不含最低收費），用於快速估算。"""
    return (costs["fee_pct"] * 2 + costs.get("sell_tax_pct", 0.0)) / 100


def can_enter_now(mk: dict, now_utc: pd.Timestamp) -> tuple[bool, str]:
    local = now_utc.tz_convert(mk["tz"])
    if not mk.get("sessions"):
        return True, "24 小時市場"
    if local.weekday() >= 5:
        return False, "週末休市"
    m = local.hour * 60 + local.minute
    if not any(hhmm(a) <= m < hhmm(b) for a, b in mk["sessions"]):
        return False, f"非交易時段（當地 {local:%H:%M}）"
    le = hhmm(mk.get("last_entry", ""))
    if le is not None and m >= le:
        return False, f"超過最後進場時間 {mk['last_entry']}"
    return True, f"交易中（當地 {local:%H:%M}，國定假日未檢查）"
