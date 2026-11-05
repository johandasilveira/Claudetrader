import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from claudetrader.config import Settings
from claudetrader.executor import Executor
from claudetrader.journal import Journal
from claudetrader.models import AccountSnapshot, OpenOrderInfo, PositionInfo
from claudetrader.plan import main as validate_plan
from claudetrader.risk import RiskManager


def settings(tmp_path, **kw):
    base = dict(symbols=["AAPL", "TSLA", "NVDA"], dry_run=False, decision_source="analyst",
                orders_dir=str(tmp_path / "orders"), allow_fractional=True, min_order_value=80,
                max_order_value=130, max_position_value=150, max_open_positions=2, max_daily_loss=12,
                max_orders_per_day=2, stop_loss_pct=3, take_profit_pct=5)
    base.update(kw)
    return Settings(**base)


class FakeBroker:
    def __init__(self, positions=None, open_orders=None, prices=None, funds=430.0):
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.prices = prices or {"AAPL": 100.0, "TSLA": 400.0, "NVDA": 200.0}
        self.funds = funds
        self.orders = []
        self.ib = SimpleNamespace(sleep=lambda s: None, isConnected=lambda: True)

    def snapshot(self, include_market=True):
        return AccountSnapshot(account="U1", net_liquidation=self.funds, available_funds=self.funds, daily_pnl=0,
                               positions=self.positions, open_orders=self.open_orders, market=[],
                               currency="EUR", timestamp=datetime.now(timezone.utc).isoformat())

    def last_price(self, symbol):
        return self.prices.get(symbol)

    def place_order(self, symbol, action, quantity, order_type, limit_price):
        self.orders.append((symbol, action, quantity, order_type, limit_price))
        return SimpleNamespace(order=SimpleNamespace(orderId=len(self.orders)),
                               orderStatus=SimpleNamespace(status="Submitted"))


def write_plan(tmp_path, orders, plan_id="2026-11-03T1505", minutes=90, created=None):
    folder = tmp_path / "orders"
    folder.mkdir(exist_ok=True)
    created = created or datetime.now(timezone.utc)
    plan = {"id": plan_id, "created_at": created.isoformat(),
            "expires_at": (created + timedelta(minutes=minutes)).isoformat(),
            "market_summary": "test", "orders": orders}
    path = folder / f"{plan_id}.json"
    path.write_text(json.dumps(plan))
    return path


def make(tmp_path, broker, **kw):
    s = settings(tmp_path, **kw)
    return Executor(s, broker, None, RiskManager(s), Journal(str(tmp_path / "journal.jsonl")))


BUY_TSLA = {"symbol": "tsla", "action": "buy", "notional_usd": 120, "rationale": "repli dans tendance haussière"}


def test_plan_is_executed_once_with_quantity_from_live_price(tmp_path):
    write_plan(tmp_path, [BUY_TSLA])
    broker = FakeBroker()
    ex = make(tmp_path, broker)
    ex.run_cycle()
    assert broker.orders == [("TSLA", "buy", 0.3, "limit", 400.8)]  # 120/400, limite à +0,2 %
    ex.run_cycle()
    ex.run_cycle()
    assert len(broker.orders) == 1  # jamais rejoué


def test_plan_not_replayed_after_restart(tmp_path):
    write_plan(tmp_path, [BUY_TSLA])
    broker = FakeBroker()
    make(tmp_path, broker).run_cycle()
    make(tmp_path, broker).run_cycle()  # nouvel exécutant, même journal
    assert len(broker.orders) == 1


def test_expired_plan_is_ignored(tmp_path):
    write_plan(tmp_path, [BUY_TSLA], created=datetime.now(timezone.utc) - timedelta(hours=3), minutes=90)
    broker = FakeBroker()
    make(tmp_path, broker).run_cycle()
    assert broker.orders == []


def test_invalid_file_is_reported_once_and_skipped(tmp_path):
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "casse.json").write_text("{pas du json")
    broker = FakeBroker()
    ex = make(tmp_path, broker)
    ex.run_cycle()
    ex.run_cycle()
    lines = (tmp_path / "journal.jsonl").read_text().splitlines()
    assert sum('"status": "invalid"' in l for l in lines) == 1
    assert broker.orders == []


def test_buy_outside_whitelist_is_rejected(tmp_path):
    write_plan(tmp_path, [{"symbol": "GME", "action": "buy", "notional_usd": 100, "rationale": "x"}])
    broker = FakeBroker(prices={"GME": 20.0})
    make(tmp_path, broker).run_cycle()
    assert broker.orders == []


def test_sell_without_quantity_sells_whole_position(tmp_path):
    pos = PositionInfo(symbol="NVDA", quantity=0.65, avg_cost=200, market_value=130, unrealized_pnl=0,
                       market_price=200)
    write_plan(tmp_path, [{"symbol": "NVDA", "action": "sell", "rationale": "essoufflement"}])
    broker = FakeBroker(positions=[pos])
    make(tmp_path, broker).run_cycle()
    assert broker.orders == [("NVDA", "sell", 0.65, "limit", 199.6)]


def test_sell_is_never_blocked_by_entry_limits(tmp_path):
    # Titre retiré de la liste, position au-dessus du montant max par ordre, quota d'ordres atteint.
    pos = PositionInfo(symbol="META", quantity=0.5, avg_cost=600, market_value=300, unrealized_pnl=0, market_price=600)
    write_plan(tmp_path, [{"symbol": "META", "action": "sell", "rationale": "sortie"}])
    broker = FakeBroker(positions=[pos], prices={"META": 600.0})
    ex = make(tmp_path, broker, max_orders_per_day=0)
    ex.run_cycle()
    assert broker.orders and broker.orders[0][:3] == ("META", "sell", 0.5)


def test_mechanical_stop_loss(tmp_path):
    pos = PositionInfo(symbol="AAPL", quantity=1.2, avg_cost=100, market_value=116.4, unrealized_pnl=-3.6,
                       market_price=97.0)  # -3 %
    broker = FakeBroker(positions=[pos])
    make(tmp_path, broker).run_cycle()
    assert broker.orders == [("AAPL", "sell", 1.2, "market", None)]


def test_mechanical_take_profit(tmp_path):
    pos = PositionInfo(symbol="AAPL", quantity=1.2, avg_cost=100, market_value=126, unrealized_pnl=6,
                       market_price=105.0)  # +5 %
    broker = FakeBroker(positions=[pos])
    make(tmp_path, broker).run_cycle()
    assert broker.orders == [("AAPL", "sell", 1.2, "limit", 104.79)]


def test_no_mechanical_exit_inside_band_or_when_sell_pending(tmp_path):
    inside = PositionInfo(symbol="AAPL", quantity=1, avg_cost=100, market_value=102, unrealized_pnl=2,
                          market_price=102.0)
    losing = PositionInfo(symbol="TSLA", quantity=0.3, avg_cost=400, market_value=110, unrealized_pnl=-10,
                          market_price=370.0)
    pending = OpenOrderInfo(symbol="TSLA", action="SELL", quantity=0.3, order_type="MKT", limit_price=None,
                            status="Submitted")
    broker = FakeBroker(positions=[inside, losing], open_orders=[pending])
    make(tmp_path, broker).run_cycle()
    assert broker.orders == []


def test_dry_run_sends_nothing(tmp_path):
    write_plan(tmp_path, [BUY_TSLA])
    broker = FakeBroker()
    make(tmp_path, broker, dry_run=True).run_cycle()
    assert broker.orders == []


# ---- validation des plans (outil de l'analyste) ----

def test_validate_plan_cli(tmp_path, capsys):
    settings_file = tmp_path / "settings.env"
    settings_file.write_text("SYMBOLS=AAPL,TSLA\n")
    ok = write_plan(tmp_path, [BUY_TSLA], plan_id="p1")
    assert validate_plan([str(ok), str(settings_file)]) == 0
    bad_symbol = write_plan(tmp_path, [{"symbol": "GME", "action": "buy", "notional_usd": 100, "rationale": "x"}],
                            plan_id="p2")
    assert validate_plan([str(bad_symbol), str(settings_file)]) == 1
    no_size = write_plan(tmp_path, [{"symbol": "TSLA", "action": "buy", "rationale": "x"}], plan_id="p3")
    assert validate_plan([str(no_size), str(settings_file)]) == 1
    mismatch = write_plan(tmp_path, [BUY_TSLA], plan_id="p4")
    mismatch.rename(tmp_path / "orders" / "autre.json")
    assert validate_plan([str(tmp_path / "orders" / "autre.json"), str(settings_file)]) == 1


class RejectingBroker(FakeBroker):
    def place_order(self, symbol, action, quantity, order_type, limit_price):
        self.orders.append((symbol, action, quantity, order_type, limit_price))
        entry = SimpleNamespace(status="Cancelled", errorCode=10243,
                                message="Fractional-sized order cannot be placed via API.")
        return SimpleNamespace(order=SimpleNamespace(orderId=99), orderStatus=SimpleNamespace(status="Cancelled"),
                               log=[entry])


def test_cancelled_order_is_recorded_as_rejected_with_reason(tmp_path):
    write_plan(tmp_path, [BUY_TSLA])
    broker = RejectingBroker()
    ex = make(tmp_path, broker)
    ex.run_cycle()
    lines = [json.loads(l) for l in (tmp_path / "journal.jsonl").read_text().splitlines()]
    rejected = [l for l in lines if l["kind"] == "order_rejected"]
    assert len(rejected) == 1 and "10243" in rejected[0]["reason"]
    assert not any(l["kind"] == "order" for l in lines)        # pas compté comme ordre passé
    assert ex.journal.orders_today() == 0                      # le quota du jour n'est pas consommé
