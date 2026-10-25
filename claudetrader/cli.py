"""Interface en ligne de commande : `claudetrader status|once|run`."""

from __future__ import annotations

import argparse
import logging
import sys

from .agent import ClaudeAgent
from .bot import TradingBot
from .broker import IBKRBroker
from .config import load_settings
from .journal import Journal
from .risk import RiskManager


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("ib_async").setLevel(logging.WARNING)


def _banner(settings) -> None:
    mode = "DRY_RUN (aucun ordre envoyé)" if settings.dry_run else "ORDRES RÉELS ACTIVÉS"
    compte = "RÉEL" if settings.is_live else "PAPIER"
    print(f"Claudetrader | port {settings.ib_port} ({compte}) | {mode} | modèle {settings.claude_model}")
    print(f"Univers : {', '.join(settings.symbols)} | limites : ordre {settings.max_order_value:g}, "
          f"position {settings.max_position_value:g}, perte/jour {settings.max_daily_loss:g}")


def cmd_status(settings) -> int:
    broker = IBKRBroker(settings)
    broker.connect()
    try:
        snap = broker.snapshot()
    finally:
        broker.disconnect()
    print(f"\nCompte {snap.account} ({snap.currency})")
    print(f"  Valeur nette      : {snap.net_liquidation:,.2f}")
    print(f"  Liquidités dispo  : {snap.available_funds:,.2f}")
    print(f"  PnL du jour       : {snap.daily_pnl:,.2f}")
    print("\nPositions :" if snap.positions else "\nAucune position.")
    for p in snap.positions:
        print(f"  {p.symbol:6} {p.quantity:>8g} @ {p.avg_cost:.2f}  valeur {p.market_value:,.2f}  PnL {p.unrealized_pnl:,.2f}")
    print("\nOrdres ouverts :" if snap.open_orders else "\nAucun ordre ouvert.")
    for o in snap.open_orders:
        print(f"  {o.symbol:6} {o.action} {o.quantity:g} {o.order_type} {o.limit_price or ''} [{o.status}]")
    print("\nDerniers cours :")
    for m in snap.market:
        print(f"  {m.symbol:6} {m.last_price if m.last_price is not None else 'n/a'}  ({len(m.bars)} bougies)")
    return 0


def _build_bot(settings) -> tuple[TradingBot, IBKRBroker]:
    broker = IBKRBroker(settings)
    agent = ClaudeAgent(settings)
    bot = TradingBot(settings, broker, agent, RiskManager(settings), Journal(settings.journal_path))
    return bot, broker


def cmd_once(settings) -> int:
    bot, broker = _build_bot(settings)
    broker.connect()
    try:
        bot.run_cycle()
    finally:
        broker.disconnect()
    return 0


def cmd_run(settings) -> int:
    bot, broker = _build_bot(settings)
    broker.connect()
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        print("\nArrêt demandé.")
    finally:
        broker.disconnect()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claudetrader", description="Bot de trading Claude + Interactive Brokers")
    parser.add_argument("command", choices=["status", "once", "run"],
                        help="status : lit le compte sans appeler Claude | once : un seul cycle | run : boucle continue")
    parser.add_argument("--env", default=".env", help="fichier .env à charger (défaut : .env)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    try:
        settings = load_settings(args.env)
    except ValueError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2
    _banner(settings)

    if not settings.dry_run:
        print("\nATTENTION : DRY_RUN=false, les ordres approuvés seront envoyés à IBKR.")

    handlers = {"status": cmd_status, "once": cmd_once, "run": cmd_run}
    try:
        return handlers[args.command](settings)
    except (ConnectionRefusedError, TimeoutError, OSError) as exc:
        print(
            f"\nImpossible de joindre IB Gateway sur {settings.ib_host}:{settings.ib_port} ({exc}).\n"
            "Vérifie que IB Gateway est lancé, connecté, que l'API est activée et que le port correspond "
            "(4002 papier / 4001 réel pour IB Gateway, 7497 / 7496 pour TWS).",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
