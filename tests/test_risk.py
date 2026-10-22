from claudetrader.config import Settings
from claudetrader.models import AccountSnapshot, Decision, OpenOrderInfo, PositionInfo, SymbolData
from claudetrader.risk import RiskManager


def make_settings(**overrides) -> Settings:
    base = dict(
        symbols=["AAPL", "MSFT"],
        max_order_value=1000.0,
        max_position_value=3000.0,
        max_daily_loss=200.0,
        max_orders_per_day=6,
        max_limit_deviation_pct=3.0,
        allow_short=False,
    )
    base.update(overrides)
    return Settings(**base)


def make_snapshot(**overrides) -> AccountSnapshot:
    base = dict(
        account="DU123",
        net_liquidation=10000.0,
        available_funds=5000.0,
        daily_pnl=0.0,
        positions=[],
        open_orders=[],
        market=[
            SymbolData(symbol="AAPL", last_price=100.0, bars=[]),
            SymbolData(symbol="MSFT", last_price=200.0, bars=[]),
        ],
        currency="USD",
        timestamp="2026-10-22T14:00:00+00:00",
    )
    base.update(overrides)
    return AccountSnapshot(**base)


def decision(**overrides) -> Decision:
    base = dict(symbol="AAPL", action="buy", quantity=5, order_type="limit", limit_price=100.0, confidence=0.7, rationale="test")
    base.update(overrides)
    return Decision(**base)


def test_hold_is_never_executed():
    v = RiskManager(make_settings()).check(decision(action="hold", quantity=0), make_snapshot(), 0)
    assert not v.approved and v.reason == "hold"


def test_buy_within_limits_is_approved():
    v = RiskManager(make_settings()).check(decision(), make_snapshot(), 0)
    assert v.approved, v.reason


def test_symbol_outside_universe_is_rejected():
    v = RiskManager(make_settings()).check(decision(symbol="TSLA"), make_snapshot(), 0)
    assert not v.approved and "univers" in v.reason


def test_order_value_cap():
    v = RiskManager(make_settings()).check(decision(quantity=11), make_snapshot(), 0)  # 1100 > 1000
    assert not v.approved and "MAX_ORDER_VALUE" in v.reason


def test_position_value_cap_counts_existing_and_pending():
    snap = make_snapshot(
        positions=[PositionInfo(symbol="AAPL", quantity=20, avg_cost=90, market_value=2000, unrealized_pnl=200)],
        open_orders=[OpenOrderInfo(symbol="AAPL", action="BUY", quantity=5, order_type="LMT", limit_price=99, status="Submitted")],
    )
    # 20 + 5 + 6 = 31 actions x 100 = 3100 > 3000
    v = RiskManager(make_settings()).check(decision(quantity=6), snap, 0)
    assert not v.approved and "MAX_POSITION_VALUE" in v.reason
    v = RiskManager(make_settings()).check(decision(quantity=5), snap, 0)
    assert v.approved


def test_daily_loss_kill_switch_blocks_buys_but_not_sells():
    snap = make_snapshot(
        daily_pnl=-250.0,
        positions=[PositionInfo(symbol="AAPL", quantity=10, avg_cost=90, market_value=1000, unrealized_pnl=100)],
    )
    rm = RiskManager(make_settings())
    assert rm.kill_switch(snap)
    assert not rm.check(decision(), snap, 0).approved
    assert rm.check(decision(action="sell", quantity=10), snap, 0).approved


def test_max_orders_per_day():
    v = RiskManager(make_settings()).check(decision(), make_snapshot(), 6)
    assert not v.approved and "par jour" in v.reason


def test_insufficient_funds():
    v = RiskManager(make_settings()).check(decision(quantity=5), make_snapshot(available_funds=100.0), 0)
    assert not v.approved and "Liquidités" in v.reason


def test_limit_price_too_far_from_market():
    v = RiskManager(make_settings()).check(decision(limit_price=90.0), make_snapshot(), 0)  # 10 % sous le cours
    assert not v.approved and "Prix limite" in v.reason


def test_limit_order_requires_price():
    v = RiskManager(make_settings()).check(decision(limit_price=None), make_snapshot(), 0)
    assert not v.approved


def test_no_short_selling_by_default():
    snap = make_snapshot(positions=[PositionInfo(symbol="AAPL", quantity=3, avg_cost=90, market_value=300, unrealized_pnl=30)])
    v = RiskManager(make_settings()).check(decision(action="sell", quantity=5), snap, 0)
    assert not v.approved and "découvert" in v.reason
    v = RiskManager(make_settings(allow_short=True)).check(decision(action="sell", quantity=5), snap, 0)
    assert v.approved


def test_missing_price_is_rejected():
    snap = make_snapshot(market=[SymbolData(symbol="AAPL", last_price=None, bars=[]), SymbolData(symbol="MSFT", last_price=200.0, bars=[])])
    v = RiskManager(make_settings()).check(decision(), snap, 0)
    assert not v.approved and "prix" in v.reason.lower()


def test_market_order_uses_last_price_for_notional():
    v = RiskManager(make_settings()).check(decision(order_type="market", limit_price=None, quantity=10), make_snapshot(), 0)
    assert v.approved
    v = RiskManager(make_settings()).check(decision(order_type="market", limit_price=None, quantity=11), make_snapshot(), 0)
    assert not v.approved
