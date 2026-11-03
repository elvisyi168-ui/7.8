"""風控：由停損距離反推部位、檢查強平價、槓桿上限。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Sizing:
    ok: bool
    qty: float = 0.0
    notional: float = 0.0
    margin: float = 0.0
    leverage_used: float = 0.0
    liq_price: float = 0.0
    risk_amount: float = 0.0
    reason: str = ""


def liquidation_price(entry: float, side: int, leverage: float, mmr: float) -> float:
    """逐倉近似強平價（未含手續費，實際以交易所為準）。"""
    return entry * (1 - 1 / leverage + mmr) if side > 0 else entry * (1 + 1 / leverage - mmr)


def size_position(equity: float, entry: float, stop: float, side: int,
                  risk_pct: float, max_leverage: float, mmr: float, fee_pct: float = 0.0,
                  max_fee_r: float = 1.0) -> Sizing:
    dist = abs(entry - stop)
    if dist <= 0 or (side > 0 and stop >= entry) or (side < 0 and stop <= entry):
        return Sizing(False, reason="停損位置錯誤")
    risk_amt = equity * risk_pct / 100
    # 停損時的虧損 = 價差 + 進出兩次手續費
    fee_unit = entry * fee_pct / 100 * 2
    per_unit_loss = dist + fee_unit
    if fee_unit / per_unit_loss > max_fee_r:
        return Sizing(False, reason=f"停損太窄，手續費佔風險 {fee_unit / per_unit_loss:.0%}，不划算")
    qty = risk_amt / per_unit_loss
    notional = qty * entry
    cap = equity * max_leverage
    if notional > cap:  # 槓桿上限優先，寧可少賺
        notional = cap
        qty = notional / entry
    # 交易所槓桿設為 max_leverage（逐倉），保證金 = 名目 / 槓桿；實際曝險倍數另計
    margin = notional / max_leverage
    liq = liquidation_price(entry, side, max_leverage, mmr)
    if (side > 0 and liq >= stop) or (side < 0 and liq <= stop):
        return Sizing(False, reason=f"強平價 {liq:.4f} 比停損更近，請降低槓桿或縮小停損")
    return Sizing(True, qty, notional, margin, notional / equity, liq, qty * per_unit_loss)
