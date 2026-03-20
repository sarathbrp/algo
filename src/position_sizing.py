"""
Position sizing: risk per trade 0.25%–1%, max open risk cap, per-symbol/sector exposure.

- Risk per trade: 0.25%–1.0% of account.
- Max open risk: cap total at-risk (sum of stop distances) to e.g. 2%–5%.
- Max notional per symbol: min(% of equity, optional max_position_dollar_cap).
- Max exposure per sector to avoid hidden concentration.
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class PositionSizingResult:
    shares: int
    notional: float
    risk_amount: float
    risk_pct: float
    reject_reason: str | None = None


class PositionSizer:
    def __init__(self, config: dict[str, Any]):
        ps = config.get("position_sizing", {})
        self.risk_per_trade_pct = float(ps.get("risk_per_trade_pct", 0.5))
        self.max_open_risk_pct = float(ps.get("max_open_risk_pct", 3.0))
        self.max_exposure_per_symbol_pct = float(ps.get("max_exposure_per_symbol_pct", 20.0))
        cap_raw = ps.get("max_position_dollar_cap")
        self.max_position_dollar_cap: float | None = (
            float(cap_raw) if cap_raw is not None and cap_raw != "" else None
        )
        self.max_exposure_per_sector_pct = float(ps.get("max_exposure_per_sector_pct", 40.0))
        hvr = ps.get("high_vol_reduction", {})
        self.high_vol_enabled = bool(hvr.get("enabled", False))
        self.high_vol_atr_threshold = float(hvr.get("atr_pct_threshold", 2.0))
        self.high_vol_size_multiplier = float(hvr.get("size_multiplier", 0.5))

    def size_position(
        self,
        account_equity: float,
        price: float,
        stop_distance_pct: float,
        symbol: str,
        current_positions: dict[str, Any],
        sector_exposure_pct: dict[str, float],
        symbol_sector: dict[str, str] | None = None,
        atr_pct: float | None = None,
        regime_size_multiplier: float | None = None,
    ) -> PositionSizingResult:
        """
        Compute shares so that risk = risk_per_trade_pct of account.
        Reject if adding this trade would exceed max open risk or exposure limits.
        """
        risk_amount = account_equity * (self.risk_per_trade_pct / 100.0)
        if stop_distance_pct <= 0:
            return PositionSizingResult(
                shares=0,
                notional=0.0,
                risk_amount=0.0,
                risk_pct=0.0,
                reject_reason="invalid stop_distance_pct",
            )

        risk_per_share = price * (stop_distance_pct / 100.0)
        if risk_per_share <= 0:
            return PositionSizingResult(
                shares=0,
                notional=0.0,
                risk_amount=0.0,
                risk_pct=0.0,
                reject_reason="risk_per_share <= 0",
            )

        shares_by_risk = int(risk_amount / risk_per_share)
        if shares_by_risk <= 0:
            return PositionSizingResult(
                shares=0,
                notional=0.0,
                risk_amount=0.0,
                risk_pct=0.0,
                reject_reason="shares <= 0 (risk too small vs stop)",
            )

        # Cap by max symbol exposure (% equity) and optional USD cap; trade at smaller size
        pct_cap = account_equity * (self.max_exposure_per_symbol_pct / 100.0)
        if self.max_position_dollar_cap is not None and self.max_position_dollar_cap > 0:
            max_notional = min(pct_cap, self.max_position_dollar_cap)
        else:
            max_notional = pct_cap
        notional_by_risk = shares_by_risk * price
        if notional_by_risk > max_notional:
            notional = max_notional
            shares = int(notional / price)
            if shares <= 0:
                return PositionSizingResult(
                    shares=0,
                    notional=0.0,
                    risk_amount=0.0,
                    risk_pct=0.0,
                    reject_reason="exposure cap yields zero shares",
                )
            risk_amount = shares * risk_per_share
            risk_pct = (risk_amount / account_equity) * 100.0
        else:
            shares = shares_by_risk
            notional = notional_by_risk
            risk_pct = self.risk_per_trade_pct

        # Position sizing reduction during high volatility regimes
        if (
            self.high_vol_enabled
            and atr_pct is not None
            and atr_pct > self.high_vol_atr_threshold
        ):
            shares = max(1, int(shares * self.high_vol_size_multiplier))
            notional = shares * price
            risk_amount = shares * risk_per_share
            risk_pct = (risk_amount / account_equity) * 100.0

        # Market regime: scale size by regime (bullish/neutral/defensive)
        if regime_size_multiplier is not None and regime_size_multiplier > 0:
            shares = max(1, int(shares * regime_size_multiplier))
            notional = shares * price
            risk_amount = shares * risk_per_share
            risk_pct = (risk_amount / account_equity) * 100.0

        exposure_pct = (notional / account_equity) * 100.0

        sector = (symbol_sector or {}).get(symbol, "unknown")
        current_sector_pct = sector_exposure_pct.get(sector, 0.0)
        if current_sector_pct + exposure_pct > self.max_exposure_per_sector_pct:
            return PositionSizingResult(
                shares=0,
                notional=0.0,
                risk_amount=0.0,
                risk_pct=0.0,
                reject_reason=f"sector {sector} would exceed {self.max_exposure_per_sector_pct}%",
            )

        return PositionSizingResult(
            shares=shares,
            notional=notional,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
        )

    def total_open_risk_pct(
        self,
        account_equity: float,
        positions_with_stops: list[tuple[float, float]],
    ) -> float:
        """Sum of (position_value * stop_distance_pct) / equity as pct."""
        if account_equity <= 0:
            return 0.0
        total_risk = sum(
            (notional * (stop_pct / 100.0)) / account_equity * 100.0
            for notional, stop_pct in positions_with_stops
        )
        return total_risk

    def would_exceed_max_open_risk(
        self,
        account_equity: float,
        current_open_risk_pct: float,
        new_trade_risk_pct: float,
    ) -> bool:
        return (current_open_risk_pct + new_trade_risk_pct) > self.max_open_risk_pct
