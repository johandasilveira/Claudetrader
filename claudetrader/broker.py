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

    def connect(self) -> None:
        s = self.settings
        log.info("Connexion à IBKR %s:%s (clientId=%s, readonly=%s)", s.ib_host, s.ib_port, s.ib_client_id, s.dry_run)
        # En dry-run on se connecte en lecture seule : la passerelle elle-même refusera tout ordre.
        self.ib.connect(s.ib_host, s.ib_port, clientId=s.ib_client_id, readonly=s.dry_run, account=s.ib_account, timeout=10)
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

    def _account_value(self, tag: str) -> float:
        for v in self.ib.accountValues(self.account):
            if v.tag == tag and v.currency in (self.settings.currency, "BASE", ""):
                try:
                    return float(v.value)
                except ValueError:
                    continue
        return 0.0

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
        return AccountSnapshot(
            account=self.account,
            net_liquidation=self._account_value("NetLiquidation"),
            available_funds=self._account_value("AvailableFunds"),
            daily_pnl=self._daily_pnl(),
            positions=positions,
            open_orders=open_orders,
            market=market,
            currency=self.settings.currency,
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
