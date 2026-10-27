"""Boucle principale : lire -> décider -> filtrer -> exécuter -> journaliser."""

from __future__ import annotations

import logging
import time

from . import market
from .agent import ClaudeAgent
from .broker import IBKRBroker
from .config import Settings
from .journal import Journal
from .notify import Notifier
from .risk import RiskManager

log = logging.getLogger(__name__)


class TradingBot:
    def __init__(self, settings: Settings, broker: IBKRBroker, agent: ClaudeAgent, risk: RiskManager, journal: Journal,
                 notifier: Notifier | None = None):
        self.s = settings
        self.broker = broker
        self.agent = agent
        self.risk = risk
        self.journal = journal
        self.notifier = notifier or Notifier()
        self._kill_notified_on: str | None = None

    def run_cycle(self) -> None:
        snap = self.broker.snapshot()
        orders_today = self.journal.orders_today()
        log.info(
            "Compte %s | valeur %.2f | liquidités %.2f | PnL jour %.2f | %d positions | %d ordres ouverts | %d ordres envoyés aujourd'hui",
            snap.account, snap.net_liquidation, snap.available_funds, snap.daily_pnl,
            len(snap.positions), len(snap.open_orders), orders_today,
        )
        self.journal.write("snapshot", snap.model_dump(exclude={"market"}))

        kill = self.risk.kill_switch(snap)
        if kill:
            log.warning("KILL SWITCH : %s", kill)
            self.journal.write("kill_switch", {"reason": kill})
            today = snap.timestamp[:10]
            if self._kill_notified_on != today:
                self.notifier.send(f"Coupe-circuit déclenché : {kill}", high=True, tags="rotating_light")
                self._kill_notified_on = today

        try:
            plan = self.agent.decide(snap, orders_today)
        except Exception as exc:  # erreur API, refus, réponse invalide
            log.error("Décision impossible ce cycle : %s", exc)
            self.journal.write("agent_error", {"error": str(exc)})
            return

        log.info("Marché selon Claude : %s", plan.market_summary)
        self.journal.write("plan", plan.model_dump())

        for decision in plan.decisions:
            verdict = self.risk.check(decision, snap, orders_today)
            tag = "APPROUVÉ" if verdict.approved else "REJETÉ"
            if decision.action == "hold":
                log.info("%s : hold (%s)", decision.symbol, decision.rationale)
                continue
            log.info(
                "%s : %s %s x%g %s%s -> %s (%s)",
                decision.symbol, tag, decision.action, decision.quantity, decision.order_type,
                f" @ {decision.limit_price}" if decision.limit_price else "", verdict.reason, decision.rationale,
            )
            self.journal.write("verdict", {"decision": decision.model_dump(), "approved": verdict.approved, "reason": verdict.reason})
            if not verdict.approved:
                continue

            if self.s.dry_run:
                log.info("DRY_RUN : ordre non envoyé.")
                self.journal.write("dry_run_order", decision.model_dump())
                continue

            try:
                trade = self.broker.place_order(
                    decision.symbol, decision.action, decision.quantity, decision.order_type, decision.limit_price
                )
            except Exception as exc:
                log.error("Échec d'envoi de l'ordre %s : %s", decision.symbol, exc)
                self.journal.write("order_error", {"decision": decision.model_dump(), "error": str(exc)})
                self.notifier.send(f"Ordre {decision.symbol} non envoyé : {exc}", high=True, tags="warning")
                continue
            orders_today += 1
            verb = "Achat" if decision.action == "buy" else "Vente"
            price = f" à {decision.limit_price:.2f} $" if decision.limit_price else " au marché"
            self.notifier.send(
                f"{verb} {decision.quantity:g} {decision.symbol}{price} "
                f"(statut IBKR : {trade.orderStatus.status}). {decision.rationale}",
                tags="moneybag" if decision.action == "sell" else "shopping_cart",
            )
            self.journal.write(
                "order",
                {
                    "decision": decision.model_dump(),
                    "order_id": trade.order.orderId,
                    "status": trade.orderStatus.status,
                },
            )

    def _sleep_until_market(self) -> bool:
        """Hors séance, attend la prochaine ouverture sans appeler Claude.

        Retourne True si le cycle doit être sauté. Un appel API hors marché ne
        peut rien décider d'utile et coûte le prix d'un cycle complet.
        """
        if not self.s.trade_hours_only or market.is_open():
            return False
        secs = market.seconds_until_open()
        opening = market.next_open()
        log.info(
            "Marché fermé. Prochaine ouverture : %s (dans %.1f h). Aucun appel à Claude d'ici là.",
            opening.strftime("%d/%m %H:%M %Z"), secs / 3600,
        )
        # On se réveille au plus tard toutes les heures pour garder la connexion vivante.
        self.broker.ib.sleep(min(secs + 30, 3600))
        return True

    def run_forever(self) -> None:
        interval = max(1, self.s.interval_minutes) * 60
        log.info(
            "Boucle démarrée, un cycle toutes les %d minutes%s. Ctrl+C pour arrêter.",
            self.s.interval_minutes, ", heures de marché uniquement" if self.s.trade_hours_only else "",
        )
        while True:
            started = time.monotonic()
            wait = interval
            try:
                if self._sleep_until_market():
                    continue
                if self.s.skip_first_minutes and (since := market.minutes_since_open()) is not None \
                        and since < self.s.skip_first_minutes:
                    log.info("Ouverture il y a %.0f min : on laisse passer les %d premières minutes.",
                             since, self.s.skip_first_minutes)
                    self.broker.ib.sleep(60 * (self.s.skip_first_minutes - since) + 10)
                    continue
                if not self.broker.ib.isConnected():
                    log.warning("Déconnecté de la passerelle (redémarrage IB Gateway ?). Reconnexion...")
                    self.journal.write("reconnect", {})
                    self.broker.connect(retries=1_000_000, retry_delay=30)
                self.run_cycle()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log.exception("Cycle en erreur : %s", exc)
                self.journal.write("cycle_error", {"error": str(exc)})
                wait = 60  # après une erreur (souvent une coupure passerelle), on réessaie vite
                if self.broker.ib.isConnected():
                    self.broker.disconnect()  # repartir d'une connexion propre au prochain tour
            elapsed = time.monotonic() - started
            # ib_async a besoin que sa boucle d'événements tourne pendant l'attente.
            self.broker.ib.sleep(max(5.0, wait - elapsed))
