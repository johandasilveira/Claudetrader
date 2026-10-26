"""Garde-fous codés en dur. Rien ne part vers IBKR sans passer par ici."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .models import AccountSnapshot, Decision


@dataclass
class RiskVerdict:
    decision: Decision
    approved: bool
    reason: str


class RiskManager:
    def __init__(self, settings: Settings):
        self.s = settings

    # ---- vérifications globales ----

    def kill_switch(self, snap: AccountSnapshot) -> str | None:
        """Retourne un motif si le bot doit cesser toute nouvelle prise de risque."""
        if snap.daily_pnl <= -self.s.max_daily_loss:
            return (
                f"Perte du jour {snap.daily_pnl:.2f} {snap.currency} au-delà de la limite "
                f"-{self.s.max_daily_loss:.2f}. Plus aucune ouverture de position aujourd'hui."
            )
        return None

    # ---- vérification d'une décision ----

    def check(self, decision: Decision, snap: AccountSnapshot, orders_today: int) -> RiskVerdict:
        d = decision
        if d.action == "hold":
            return RiskVerdict(d, False, "hold")

        if d.symbol not in self.s.symbols:
            return RiskVerdict(d, False, f"{d.symbol} n'est pas dans l'univers autorisé.")
        if d.quantity <= 0:
            return RiskVerdict(d, False, "Quantité nulle ou négative.")
        if not self.s.allow_fractional and d.quantity != int(d.quantity):
            return RiskVerdict(d, False, f"Quantité fractionnaire {d.quantity} alors que ALLOW_FRACTIONAL=false.")
        if orders_today >= self.s.max_orders_per_day:
            return RiskVerdict(d, False, f"Limite de {self.s.max_orders_per_day} ordres par jour atteinte.")

        price = self._price(d.symbol, snap)
        if price is None or price <= 0:
            return RiskVerdict(d, False, f"Pas de prix disponible pour {d.symbol}.")

        if d.order_type == "limit":
            if d.limit_price is None or d.limit_price <= 0:
                return RiskVerdict(d, False, "Ordre limite sans prix limite valide.")
            deviation = abs(d.limit_price - price) / price * 100
            if deviation > self.s.max_limit_deviation_pct:
                return RiskVerdict(
                    d, False,
                    f"Prix limite {d.limit_price:.2f} à {deviation:.1f}% du dernier cours {price:.2f} "
                    f"(max {self.s.max_limit_deviation_pct}%).",
                )
            ref_price = d.limit_price
        else:
            ref_price = price

        notional = d.quantity * ref_price
        if notional < self.s.min_order_value:
            return RiskVerdict(d, False, f"Ordre de {notional:.2f} < MIN_ORDER_VALUE {self.s.min_order_value:.2f}.")
        if notional > self.s.max_order_value:
            return RiskVerdict(d, False, f"Ordre de {notional:.2f} > MAX_ORDER_VALUE {self.s.max_order_value:.2f}.")

        current_qty = self._position_qty(d.symbol, snap)
        pending = self._pending_qty(d.symbol, snap)

        if d.action == "buy":
            kill = self.kill_switch(snap)
            if kill:
                return RiskVerdict(d, False, kill)
            held = {p.symbol for p in snap.positions if p.quantity != 0}
            if d.symbol not in held and len(held) >= self.s.max_open_positions:
                return RiskVerdict(
                    d, False,
                    f"Déjà {len(held)} positions ouvertes (max {self.s.max_open_positions}) : "
                    f"pas de nouvelle ligne sur {d.symbol}.",
                )
            if notional > snap.available_funds:
                return RiskVerdict(d, False, f"Liquidités insuffisantes ({snap.available_funds:.2f} disponibles).")
            projected = (current_qty + pending + d.quantity) * ref_price
            if projected > self.s.max_position_value:
                return RiskVerdict(
                    d, False,
                    f"Position projetée {projected:.2f} > MAX_POSITION_VALUE {self.s.max_position_value:.2f}.",
                )
        else:  # sell
            if not self.s.allow_short and d.quantity > current_qty + pending:
                return RiskVerdict(
                    d, False,
                    f"Vente de {d.quantity} alors que la position est de {current_qty:g} (vente à découvert interdite).",
                )
            if self.s.allow_short:
                projected = abs(current_qty + pending - d.quantity) * ref_price
                if projected > self.s.max_position_value:
                    return RiskVerdict(d, False, f"Position short projetée {projected:.2f} trop grande.")

        return RiskVerdict(d, True, "ok")

    # ---- helpers ----

    @staticmethod
    def _price(symbol: str, snap: AccountSnapshot) -> float | None:
        for m in snap.market:
            if m.symbol == symbol:
                return m.last_price
        return None

    @staticmethod
    def _position_qty(symbol: str, snap: AccountSnapshot) -> float:
        return sum(p.quantity for p in snap.positions if p.symbol == symbol)

    @staticmethod
    def _pending_qty(symbol: str, snap: AccountSnapshot) -> float:
        """Quantité signée des ordres encore ouverts (achat positif, vente négative)."""
        total = 0.0
        for o in snap.open_orders:
            if o.symbol != symbol:
                continue
            total += o.quantity if o.action == "BUY" else -o.quantity
        return total
