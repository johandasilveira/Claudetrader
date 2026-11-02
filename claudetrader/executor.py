"""Mode analyste : le bot exécute les plans écrits par Claude en session, sans appeler l'API.

À chaque cycle, pendant la séance :
1. il lit le compte ;
2. il applique les sorties mécaniques (stop-loss, prise de bénéfice) sur les positions ouvertes,
   pour qu'aucune position ne reste sans protection si l'analyste ne passe pas ;
3. il exécute les plans nouveaux et encore valables du dossier orders/, une seule fois chacun.

Chaque ordre passe par les mêmes garde-fous que dans le mode API.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from pathlib import Path

from .bot import TradingBot
from .models import AccountSnapshot, AnalystOrder, AnalystPlan, Decision, SymbolData

log = logging.getLogger(__name__)

LIMIT_OFFSET = 0.002  # ordre limite par défaut à 0,2 % du dernier cours, du côté qui favorise l'exécution


class Executor(TradingBot):
    def run_cycle(self) -> None:
        snap = self.broker.snapshot(include_market=False)
        orders_today = self.journal.orders_today()
        log.info(
            "Compte %s | valeur %.2f | liquidités %.2f | PnL jour %.2f | %d positions | %d ordres ouverts",
            snap.account, snap.net_liquidation, snap.available_funds, snap.daily_pnl,
            len(snap.positions), len(snap.open_orders),
        )
        self._check_kill_switch(snap)

        if self._mechanical_exits(snap, orders_today):
            snap = self.broker.snapshot(include_market=False)
            orders_today = self.journal.orders_today()
        self._process_inbox(snap, orders_today)

    # ---- sorties mécaniques ----

    def _mechanical_exits(self, snap: AccountSnapshot, orders_today: int) -> bool:
        placed = False
        for p in snap.positions:
            if p.quantity <= 0 or p.avg_cost <= 0 or not p.market_price:
                continue
            if any(o.symbol == p.symbol and o.action == "SELL" for o in snap.open_orders):
                continue  # une vente est déjà en cours sur ce titre
            pct = (p.market_price / p.avg_cost - 1) * 100
            if self.s.stop_loss_pct and pct <= -self.s.stop_loss_pct:
                decision = Decision(symbol=p.symbol, action="sell", quantity=p.quantity, order_type="market",
                                    limit_price=None, confidence=1.0,
                                    rationale=f"Stop-loss mécanique : {pct:.1f} % (seuil -{self.s.stop_loss_pct:g} %).")
            elif self.s.take_profit_pct and pct >= self.s.take_profit_pct:
                decision = Decision(symbol=p.symbol, action="sell", quantity=p.quantity, order_type="limit",
                                    limit_price=round(p.market_price * (1 - LIMIT_OFFSET), 2), confidence=1.0,
                                    rationale=f"Prise de bénéfice mécanique : +{pct:.1f} % "
                                              f"(seuil +{self.s.take_profit_pct:g} %).")
            else:
                continue
            before = orders_today
            orders_today = self._handle_decision(decision, _with_price(snap, p.symbol, p.market_price),
                                                 orders_today, source="mécanique")
            placed = placed or orders_today > before
        return placed

    # ---- plans de l'analyste ----

    def _process_inbox(self, snap: AccountSnapshot, orders_today: int) -> None:
        folder = Path(self.s.orders_dir)
        if not folder.is_dir():
            return
        seen = self.journal.seen_plans()
        now = datetime.now(timezone.utc)
        for path in sorted(folder.glob("*.json")):
            file_key = f"file:{path.name}"
            if file_key in seen:
                continue
            try:
                plan = AnalystPlan.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                log.error("Plan illisible %s : %s", path.name, exc)
                self.journal.write("plan_seen", {"key": file_key, "status": "invalid", "error": str(exc)})
                self.notifier.send(f"Plan {path.name} illisible, ignoré : {exc}", high=True, tags="warning")
                continue
            # Marqué traité AVANT exécution : en cas de plantage, on préfère rater un ordre que le doubler.
            self.journal.write("plan_seen", {"key": file_key, "plan_id": plan.id,
                                             "status": "expired" if plan.expires_at <= now else "executing"})
            if plan.expires_at <= now:
                log.info("Plan %s expiré (%s) : ignoré.", plan.id, plan.expires_at.isoformat())
                continue
            log.info("Plan %s : %s", plan.id, plan.market_summary)
            for order in plan.orders:
                decision, price = self._to_decision(order, snap)
                if decision is None:
                    continue
                orders_today = self._handle_decision(decision, _with_price(snap, order.symbol, price),
                                                     orders_today, source=f"analyste {plan.id}")
                snap = self.broker.snapshot(include_market=False)  # positions et ordres à jour

    def _to_decision(self, order: AnalystOrder, snap: AccountSnapshot) -> tuple[Decision | None, float | None]:
        price = self.broker.last_price(order.symbol)
        if not price:
            log.error("Pas de cours pour %s : ordre ignoré.", order.symbol)
            self.journal.write("verdict", {"source": "analyste", "symbol": order.symbol, "approved": False,
                                           "reason": "pas de cours disponible"})
            return None, None
        if order.action == "sell" and not order.quantity:
            quantity = sum(p.quantity for p in snap.positions if p.symbol == order.symbol)
            if quantity <= 0:
                log.info("Vente %s demandée sans position ouverte : rien à faire.", order.symbol)
                return None, price
        elif order.quantity:
            quantity = order.quantity
        else:
            quantity = order.notional_usd / price
            quantity = round(quantity, 4) if self.s.allow_fractional else float(math.floor(quantity))
        if order.order_type == "limit":
            limit = order.limit_price or price * (1 + LIMIT_OFFSET if order.action == "buy" else 1 - LIMIT_OFFSET)
            limit = round(limit, 2)
        else:
            limit = None
        return Decision(symbol=order.symbol, action=order.action, quantity=quantity, order_type=order.order_type,
                        limit_price=limit, confidence=1.0, rationale=order.rationale), price


def _with_price(snap: AccountSnapshot, symbol: str, price: float | None) -> AccountSnapshot:
    """Copie du compte où le cours du symbole est renseigné, pour les contrôles des garde-fous."""
    market = [m for m in snap.market if m.symbol != symbol] + [SymbolData(symbol=symbol, last_price=price, bars=[])]
    return snap.model_copy(update={"market": market})
