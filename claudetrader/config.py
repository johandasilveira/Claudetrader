"""Configuration du bot, lue depuis les variables d'environnement (fichier .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

LIVE_PORTS = {4001, 7496, 4003}  # 4003 = port live de l'image Docker ib-gateway
LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_THIS_TRADES_REAL_MONEY"


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass
class Settings:
    # Interactive Brokers
    ib_host: str = "127.0.0.1"
    ib_port: int = 4002
    ib_client_id: int = 7
    ib_account: str = ""
    trading_mode: str = "paper"  # paper ou live

    # Claude
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "high"

    # Univers
    symbols: list[str] = field(default_factory=lambda: ["AAPL", "MSFT", "SPY"])
    exchange: str = "SMART"
    currency: str = "USD"
    interval_minutes: int = 30
    bar_size: str = "1 hour"
    history_duration: str = "10 D"

    # Garde-fous
    max_order_value: float = 1000.0
    max_position_value: float = 3000.0
    max_daily_loss: float = 200.0
    max_orders_per_day: int = 6
    max_limit_deviation_pct: float = 3.0
    allow_short: bool = False
    allow_fractional: bool = False
    min_order_value: float = 1.0
    max_open_positions: int = 2

    # Cadence
    trade_hours_only: bool = True
    skip_first_minutes: int = 30

    # Sécurité
    dry_run: bool = True
    live_trading_confirm: str = ""

    journal_path: str = "data/journal.jsonl"

    @property
    def is_live_port(self) -> bool:
        return self.ib_port in LIVE_PORTS

    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live" or self.is_live_port

    def validate(self) -> None:
        """Lève une erreur claire si la configuration est dangereuse ou incomplète."""
        if not self.symbols:
            raise ValueError("SYMBOLS est vide : indique au moins un symbole.")
        if self.max_order_value <= 0 or self.max_position_value <= 0:
            raise ValueError("MAX_ORDER_VALUE et MAX_POSITION_VALUE doivent être positifs.")
        if self.max_order_value > self.max_position_value:
            raise ValueError("MAX_ORDER_VALUE ne peut pas dépasser MAX_POSITION_VALUE.")
        if self.max_daily_loss <= 0:
            raise ValueError("MAX_DAILY_LOSS doit être positif.")
        if self.trading_mode not in ("paper", "live"):
            raise ValueError("TRADING_MODE doit valoir paper ou live.")
        if self.is_live and not self.dry_run and self.live_trading_confirm != LIVE_CONFIRM_PHRASE:
            raise ValueError(
                f"Trading REEL demandé (TRADING_MODE={self.trading_mode}, port {self.ib_port}). Pour l'autoriser, "
                f"mets LIVE_TRADING_CONFIRM={LIVE_CONFIRM_PHRASE} dans le .env."
            )
        if not self.dry_run and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY manquante.")


def load_settings(env_file: str | None = ".env", shared_file: str | None = "settings.env") -> Settings:
    """Ordre de priorité : variables d'environnement > .env (local, secrets) > settings.env (versionné)."""
    if env_file:
        load_dotenv(env_file, override=False)
    if shared_file:
        load_dotenv(shared_file, override=False)
    symbols = [s.strip().upper() for s in os.getenv("SYMBOLS", "AAPL,MSFT,SPY").split(",") if s.strip()]
    settings = Settings(
        ib_host=os.getenv("IB_HOST", "127.0.0.1"),
        ib_port=_int("IB_PORT", 4002),
        ib_client_id=_int("IB_CLIENT_ID", 7),
        ib_account=os.getenv("IB_ACCOUNT", "").strip(),
        trading_mode=os.getenv("TRADING_MODE", "paper").strip().lower(),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5").strip(),
        claude_effort=os.getenv("CLAUDE_EFFORT", "high").strip(),
        symbols=symbols,
        exchange=os.getenv("EXCHANGE", "SMART").strip(),
        currency=os.getenv("CURRENCY", "USD").strip(),
        interval_minutes=_int("INTERVAL_MINUTES", 30),
        bar_size=os.getenv("BAR_SIZE", "1 hour").strip(),
        history_duration=os.getenv("HISTORY_DURATION", "10 D").strip(),
        max_order_value=_float("MAX_ORDER_VALUE", 1000.0),
        max_position_value=_float("MAX_POSITION_VALUE", 3000.0),
        max_daily_loss=_float("MAX_DAILY_LOSS", 200.0),
        max_orders_per_day=_int("MAX_ORDERS_PER_DAY", 6),
        max_limit_deviation_pct=_float("MAX_LIMIT_DEVIATION_PCT", 3.0),
        allow_short=_bool(os.getenv("ALLOW_SHORT"), False),
        allow_fractional=_bool(os.getenv("ALLOW_FRACTIONAL"), False),
        min_order_value=_float("MIN_ORDER_VALUE", 1.0),
        max_open_positions=_int("MAX_OPEN_POSITIONS", 2),
        trade_hours_only=_bool(os.getenv("TRADE_HOURS_ONLY"), True),
        skip_first_minutes=_int("SKIP_FIRST_MINUTES", 30),
        dry_run=_bool(os.getenv("DRY_RUN"), True),
        live_trading_confirm=os.getenv("LIVE_TRADING_CONFIRM", "").strip(),
        journal_path=os.getenv("JOURNAL_PATH", "data/journal.jsonl"),
    )
    settings.validate()
    return settings
