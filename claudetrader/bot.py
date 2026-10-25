"""Boucle principale : lire -> décider -> filtrer -> exécuter -> journaliser."""

from __future__ import annotations

import logging
import time

from .agent import ClaudeAgent
from .broker import IBKRBroker
from .config import Settings
from .journal import Journal
from .risk import RiskManager

log = logging.getLogger(__name__)


class TradingBot:
    def __init__(self, settings: Settings, broker: IBKRBroker, agent: ClaudeAgent, risk: RiskManager, journal: Journal):
        self.s = settings
        self.broker = broker
        self.agent = agent
        self.risk = risk
        self.journal = journal

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
                "%s : %s %s x%d %s%s -> %s (%s)",
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
                continue
            orders_today += 1
            self.journal.write(
                "order",
                {
                    "decision": decision.model_dump(),
                    "order_id": trade.order.orderId,
                    "status": trade.orderStatus.status,
                },
            )

    def run_forever(self) -> None:
        interval = max(1, self.s.interval_minutes) * 60
        log.info("Boucle démarrée, un cycle toutes les %d minutes. Ctrl+C pour arrêter.", self.s.interval_minutes)
        while True:
            started = time.monotonic()
            wait = interval
            try:
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
