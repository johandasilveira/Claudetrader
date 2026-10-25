"""Accès à Interactive Brokers via ib_async (IB Gateway ou TWS)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ib_async import IB, LimitOrder, MarketOrder, Stock, Trade

from .config import Settings
from .models import AccountSnapshot, Bar, OpenOrderInfo, PositionInfo, SymbolData

log = logging.getLogger(__name__)

DELAYED_MARKET_DATA = 3  # type de données : 3 = différées (gratuit), 1 = temps réel (abonnement)


class IBKRBroker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ib = IB()
        self._contracts: dict[str, Stock] = {}

    # ---- connexion ----

    def connect(self, retries: int = 1, retry_delay: float = 30.0) -> None:
        """Se connecte à la passerelle. Avec retries > 1, réessaie (utile au démarrage d'un conteneur)."""
        s = self.settings
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                log.info("Connexion à IBKR %s:%s (clientId=%s, readonly=%s) tentative %d/%d",
                         s.ib_host, s.ib_port, s.ib_client_id, s.dry_run, attempt, retries)
                # En dry-run on se connecte en lecture seule : la passerelle elle-même refusera tout ordre.
                self.ib.connect(s.ib_host, s.ib_port, clientId=s.ib_client_id, readonly=s.dry_run,
                                account=s.ib_account, timeout=15)
                break
            except (OSError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                if attempt == retries:
                    raise
                log.warning("Passerelle injoignable (%s). Nouvel essai dans %.0f s.", exc, retry_delay)
                self.ib.sleep(retry_delay)
        self.ib.reqMarketDataType(DELAYED_MARKET_DATA)
        for symbol in s.symbols:
            self._contracts[symbol] = Stock(symbol, s.exchange, s.currency)
        qualified = self.ib.qualifyContracts(*self._contracts.values())
        missing = set(s.symbols) - {c.symbol for c in qualified}
        if missing:
            raise RuntimeError(f"Contrats introuvables chez IBKR : {sorted(missing)}")
        log.info("Connecté. Compte(s) : %s", self.ib.managedAccounts())

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    @property
    def account(self) -> str:
        if self.settings.ib_account:
            return self.settings.ib_account
        accounts = self.ib.managedAccounts()
        return accounts[0] if accounts else ""

    # ---- lecture ----

    def _account_value(self, tag: str) -> tuple[float, str]:
        """Valeur d'un tag de compte et sa devise.

        IBKR exprime NetLiquidation / AvailableFunds dans la devise de base du compte
        (EUR pour un compte européen). On préfère la devise configurée, puis BASE,
        puis la première valeur disponible.
        """
        candidates: dict[str, float] = {}
        for v in self.ib.accountValues(self.account):
            if v.tag != tag:
                continue
            try:
                candidates[v.currency] = float(v.value)
            except ValueError:
                continue
        if not candidates:
            return 0.0, ""
        for cur in (self.settings.currency, "BASE", ""):
            if cur in candidates:
                return candidates[cur], cur
        cur, val = next(iter(candidates.items()))
        return val, cur

    def _daily_pnl(self) -> float:
        # Le PnL quotidien n'est pas dans accountValues : on l'interroge via reqPnL.
        pnl = self.ib.reqPnL(self.account)
        self.ib.sleep(1.5)
        value = pnl.dailyPnL
        self.ib.cancelPnL(self.account)
        if value is None or value != value:  # None ou NaN
            return 0.0
        return float(value)

    def _bars(self, symbol: str) -> list[Bar]:
        contract = self._contracts[symbol]
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=self.settings.history_duration,
            barSizeSetting=self.settings.bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,
        )
        return [
            Bar(time=str(b.date), open=b.open, high=b.high, low=b.low, close=b.close, volume=float(b.volume))
            for b in bars
        ]

    def snapshot(self) -> AccountSnapshot:
        positions = []
        for item in self.ib.portfolio(self.account):
            positions.append(
                PositionInfo(
                    symbol=item.contract.symbol,
                    quantity=float(item.position),
                    avg_cost=float(item.averageCost),
                    market_value=float(item.marketValue),
                    unrealized_pnl=float(item.unrealizedPNL),
                )
            )
        open_orders = [
            OpenOrderInfo(
                symbol=t.contract.symbol,
                action=t.order.action,
                quantity=float(t.order.totalQuantity),
                order_type=t.order.orderType,
                limit_price=float(t.order.lmtPrice) if t.order.orderType == "LMT" else None,
                status=t.orderStatus.status,
            )
            for t in self.ib.openTrades()
        ]
        market = []
        for symbol in self.settings.symbols:
            bars = self._bars(symbol)
            last = bars[-1].close if bars else None
            market.append(SymbolData(symbol=symbol, last_price=last, bars=bars))
        net_liq, net_cur = self._account_value("NetLiquidation")
        available, _ = self._account_value("AvailableFunds")
        account_cur = net_cur if net_cur not in ("", "BASE") else self.settings.currency
        if account_cur != self.settings.currency:
            log.info(
                "Devise de base du compte : %s (les titres sont en %s ; les limites s'appliquent telles quelles).",
                account_cur, self.settings.currency,
            )
        return AccountSnapshot(
            account=self.account,
            net_liquidation=net_liq,
            available_funds=available,
            daily_pnl=self._daily_pnl(),
            positions=positions,
            open_orders=open_orders,
            market=market,
            currency=account_cur,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    # ---- exécution ----

    def place_order(self, symbol: str, action: str, quantity: int, order_type: str, limit_price: float | None) -> Trade:
        contract = self._contracts[symbol]
        ib_action = "BUY" if action == "buy" else "SELL"
        if order_type == "limit":
            if limit_price is None:
                raise ValueError("Un ordre limite exige un prix limite.")
            order = LimitOrder(ib_action, quantity, round(limit_price, 2))
        else:
            order = MarketOrder(ib_action, quantity)
        order.tif = "DAY"
        order.account = self.account
        trade = self.ib.placeOrder(contract, order)
        # Laisse quelques secondes à la passerelle pour accuser réception.
        for _ in range(10):
            self.ib.sleep(0.5)
            if trade.orderStatus.status in ("Submitted", "PreSubmitted", "Filled", "Cancelled", "Inactive"):
                break
        log.info(
            "Ordre %s %s %s x%s -> %s (orderId=%s)",
            trade.orderStatus.status, ib_action, symbol, quantity, order.orderType, order.orderId,
        )
        return trade
