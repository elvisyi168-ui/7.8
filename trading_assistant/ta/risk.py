"""風控：由停損距離反推部位（含最小單位、最低手續費、稅），檢查強平價與槓桿上限。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .markets import fee, roundtrip_rate


@dataclass
class Sizing:
    ok: bool
    qty: float = 0.0
    notional: float = 0.0
    margin: float = 0.0
    leverage_used: float = 0.0
    liq_price: float = 0.0
    risk_amount: float = 0.0   # 打到停損的實際虧損（含成本）
    fees: float = 0.0          # 來回成本
    reason: str = ""


def liquidation_price(entry: float, side: int, leverage: float, mmr: float) -> float:
    """逐倉近似強平價（未含手續費，實際以交易所為準）。"""
    return entry * (1 - 1 / leverage + mmr) if side > 0 else entry * (1 + 1 / leverage - mmr)


def loss_at_stop(costs: dict, entry: float, stop: float, side: int, qty: float) -> tuple[float, float]:
    fees = fee(costs, entry, qty, side < 0) + fee(costs, stop, qty, side > 0)
    return qty * abs(entry - stop) + fees, fees


def size_position(equity: float, entry: float, stop: float, side: int, cfg: dict,
                  risk_pct: float | None = None, leverage: float | None = None) -> Sizing:
    acc, costs, mk = cfg["account"], cfg["costs"], cfg["market"]
    risk_pct = risk_pct or acc["risk_per_trade_pct"]
    lev = leverage or acc.get("max_leverage", 1)
    dist = abs(entry - stop)
    if dist <= 0 or (side > 0 and stop >= entry) or (side < 0 and stop <= entry):
        return Sizing(False, reason="停損位置錯誤")
    risk_amt = equity * risk_pct / 100
    qty = min(risk_amt / (dist + entry * roundtrip_rate(costs)), equity * lev / entry)
    step = mk.get("qty_step", 0) or 0
    if step:
        qty = math.floor(qty / step + 1e-9) * step
        while qty > 0 and loss_at_stop(costs, entry, stop, side, qty)[0] > risk_amt:
            qty -= step  # 最低手續費可能讓實際虧損超標，逐單位縮小
    if qty <= 0:
        return Sizing(False, reason="風險額度買不到最小單位（停損太遠或資金太小）")
    loss, fees = loss_at_stop(costs, entry, stop, side, qty)
    if fees / loss > acc.get("max_fee_r", 1.0):
        return Sizing(False, qty=qty, fees=fees, risk_amount=loss,
                      reason=f"成本佔風險 {fees / loss:.0%}，停損太窄或部位太小，不划算")
    notional = qty * entry
    liq = 0.0
    if lev > 1:
        liq = liquidation_price(entry, side, lev, acc["maintenance_margin_rate"])
        if (side > 0 and liq >= stop) or (side < 0 and liq <= stop):
            return Sizing(False, reason=f"強平價 {liq:.6g} 比停損更近，請降低槓桿或縮小停損")
    return Sizing(True, qty, notional, notional / lev, notional / equity, liq, loss, fees)
