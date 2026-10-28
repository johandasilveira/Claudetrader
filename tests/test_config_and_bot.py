import json
from types import SimpleNamespace

import pytest

from claudetrader.agent import ClaudeAgent
from claudetrader.bot import TradingBot
from claudetrader.config import LIVE_CONFIRM_PHRASE, Settings, load_settings
from claudetrader.journal import Journal
from claudetrader.models import AccountSnapshot, Decision, SymbolData, TradePlan
from claudetrader.risk import RiskManager


# ---- configuration ----

def test_live_port_requires_confirmation(monkeypatch):
    monkeypatch.setenv("IB_PORT", "4001")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("LIVE_TRADING_CONFIRM", raising=False)
    with pytest.raises(ValueError, match="REEL"):
        load_settings(env_file=None, shared_file=None)
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", LIVE_CONFIRM_PHRASE)
    assert load_settings(env_file=None, shared_file=None).is_live_port


def test_trading_mode_live_requires_confirmation(monkeypatch):
    monkeypatch.setenv("IB_PORT", "4002")
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("LIVE_TRADING_CONFIRM", raising=False)
    with pytest.raises(ValueError, match="REEL"):
        load_settings(env_file=None, shared_file=None)
    monkeypatch.setenv("LIVE_TRADING_CONFIRM", LIVE_CONFIRM_PHRASE)
    assert load_settings(env_file=None, shared_file=None).is_live


def test_live_port_in_dry_run_is_allowed(monkeypatch):
    monkeypatch.setenv("IB_PORT", "4001")
    monkeypatch.setenv("DRY_RUN", "true")
    assert load_settings(env_file=None, shared_file=None).dry_run


def test_defaults_are_paper_and_dry_run(monkeypatch):
    for k in ("IB_PORT", "DRY_RUN", "SYMBOLS", "TRADING_MODE", "LIVE_TRADING_CONFIRM"):
        monkeypatch.delenv(k, raising=False)
    s = load_settings(env_file=None, shared_file=None)
    assert s.ib_port == 4002 and s.dry_run and s.symbols == ["AAPL", "MSFT", "SPY"]


def test_order_cap_cannot_exceed_position_cap():
    with pytest.raises(ValueError):
        Settings(max_order_value=5000, max_position_value=1000).validate()


# ---- journal ----

def test_journal_counts_orders_today_only(tmp_path):
    j = Journal(str(tmp_path / "j.jsonl"))
    j.write("order", {"x": 1})
    j.write("dry_run_order", {"x": 2})
    with j.path.open("a") as f:
        f.write(json.dumps({"ts": "2000-01-01T00:00:00+00:00", "kind": "order"}) + "\n")
    assert j.orders_today() == 1


# ---- agent : construction du message et parsing ----

def snapshot() -> AccountSnapshot:
    return AccountSnapshot(
        account="DU1", net_liquidation=10000, available_funds=5000, daily_pnl=0, positions=[], open_orders=[],
        market=[SymbolData(symbol="AAPL", last_price=100.0, bars=[])], currency="USD", timestamp="t",
    )


def test_user_message_contains_limits_and_universe():
    s = Settings(symbols=["AAPL"])
    msg = ClaudeAgent.build_user_message(snapshot(), s, orders_today=2)
    assert '"orders_already_sent_today":2' in msg and '"AAPL"' in msg


class FakeMessages:
    def __init__(self, plan, stop_reason="end_turn"):
        self.plan, self.stop_reason, self.calls = plan, stop_reason, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason=self.stop_reason, parsed_output=self.plan, stop_details=None,
            usage=SimpleNamespace(input_tokens=10, cache_read_input_tokens=0, output_tokens=5),
        )


def fake_client(plan, stop_reason="end_turn"):
    messages = FakeMessages(plan, stop_reason)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_agent_passes_fallbacks_and_structured_output():
    plan = TradePlan(market_summary="calme", decisions=[])
    client, messages = fake_client(plan)
    agent = ClaudeAgent(Settings(symbols=["AAPL"], anthropic_api_key="k"), client=client)
    assert agent.decide(snapshot(), 0) is plan
    call = messages.calls[0]
    assert call["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in call["betas"]
    assert call["output_format"] is TradePlan and call["model"] == "claude-opus-5-5"


def test_agent_raises_on_refusal():
    client, _ = fake_client(None, stop_reason="refusal")
    agent = ClaudeAgent(Settings(symbols=["AAPL"], anthropic_api_key="k"), client=client)
    with pytest.raises(RuntimeError, match="refusé"):
        agent.decide(snapshot(), 0)


# ---- boucle : dry-run et exécution ----

class FakeBroker:
    def __init__(self, snap):
        self.snap, self.orders = snap, []
        self.ib = SimpleNamespace(sleep=lambda s: None)

    def snapshot(self):
        return self.snap

    def place_order(self, symbol, action, quantity, order_type, limit_price):
        self.orders.append((symbol, action, quantity, order_type, limit_price))
        return SimpleNamespace(order=SimpleNamespace(orderId=42), orderStatus=SimpleNamespace(status="Submitted"))


class FakeAgent:
    def __init__(self, plan):
        self.plan = plan

    def decide(self, snap, orders_today):
        return self.plan


def plan_with(*decisions):
    return TradePlan(market_summary="s", decisions=list(decisions))


def buy(qty=5, **kw):
    base = dict(symbol="AAPL", action="buy", quantity=qty, order_type="limit", limit_price=100.0, confidence=0.8, rationale="r")
    base.update(kw)
    return Decision(**base)


def run_bot(settings, plan, tmp_path):
    broker = FakeBroker(snapshot())
    journal = Journal(str(tmp_path / "j.jsonl"))
    bot = TradingBot(settings, broker, FakeAgent(plan), RiskManager(settings), journal)
    bot.run_cycle()
    kinds = [json.loads(l)["kind"] for l in journal.path.read_text().splitlines()]
    return broker, kinds


def test_dry_run_sends_nothing(tmp_path):
    broker, kinds = run_bot(Settings(symbols=["AAPL"], dry_run=True), plan_with(buy()), tmp_path)
    assert broker.orders == [] and "dry_run_order" in kinds and "order" not in kinds


def test_approved_order_is_sent_when_not_dry_run(tmp_path):
    s = Settings(symbols=["AAPL"], dry_run=False, anthropic_api_key="k")
    broker, kinds = run_bot(s, plan_with(buy()), tmp_path)
    assert broker.orders == [("AAPL", "buy", 5, "limit", 100.0)] and "order" in kinds


def test_rejected_order_is_not_sent(tmp_path):
    s = Settings(symbols=["AAPL"], dry_run=False, anthropic_api_key="k")
    broker, kinds = run_bot(s, plan_with(buy(qty=50)), tmp_path)  # 5000 > max_order_value
    assert broker.orders == [] and "order" not in kinds and "verdict" in kinds


def test_daily_order_cap_is_enforced_within_one_cycle(tmp_path):
    s = Settings(symbols=["AAPL"], dry_run=False, anthropic_api_key="k", max_orders_per_day=1)
    broker, _ = run_bot(s, plan_with(buy(), buy()), tmp_path)
    assert len(broker.orders) == 1


# ---- notifications ----

class FakeNotifier:
    enabled = True

    def __init__(self):
        self.messages = []

    def send(self, message, **kw):
        self.messages.append((message, kw))


def test_order_sends_a_notification(tmp_path):
    s = Settings(symbols=["AAPL"], dry_run=False, anthropic_api_key="k")
    notifier = FakeNotifier()
    broker = FakeBroker(snapshot())
    bot = TradingBot(s, broker, FakeAgent(plan_with(buy())), RiskManager(s), Journal(str(tmp_path / "j.jsonl")), notifier)
    bot.run_cycle()
    assert len(notifier.messages) == 1
    assert notifier.messages[0][0].startswith("Achat 5 AAPL à 100.00 $")


def test_dry_run_sends_no_notification(tmp_path):
    s = Settings(symbols=["AAPL"], dry_run=True)
    notifier = FakeNotifier()
    bot = TradingBot(s, FakeBroker(snapshot()), FakeAgent(plan_with(buy())), RiskManager(s),
                     Journal(str(tmp_path / "j.jsonl")), notifier)
    bot.run_cycle()
    assert notifier.messages == []


def test_disabled_notifier_is_silent():
    from claudetrader.notify import Notifier
    n = Notifier("")
    assert not n.enabled
    n.send("rien")  # ne doit ni lever ni appeler le réseau


def test_notifier_never_raises_on_network_error():
    from claudetrader.notify import Notifier
    Notifier("sujet-de-test", server="http://127.0.0.1:9").send("ne doit pas lever")
