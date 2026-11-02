"""Structures de données partagées entre le courtier, l'agent et les garde-fous."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Bar(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class SymbolData(BaseModel):
    symbol: str
    last_price: float | None
    bars: list[Bar]


class PositionInfo(BaseModel):
    symbol: str
    quantity: float
    avg_cost: float
    market_value: float
    unrealized_pnl: float
    market_price: float | None = None


class OpenOrderInfo(BaseModel):
    symbol: str
    action: str
    quantity: float
    order_type: str
    limit_price: float | None
    status: str


class AccountSnapshot(BaseModel):
    account: str
    net_liquidation: float
    available_funds: float
    daily_pnl: float
    positions: list[PositionInfo]
    open_orders: list[OpenOrderInfo]
    market: list[SymbolData]
    currency: str
    timestamp: str


# ---- Sortie structurée de Claude ----

Action = Literal["hold", "buy", "sell"]
OrderType = Literal["market", "limit"]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(description="Symbole de l'actif, exactement comme fourni dans l'univers.")
    action: Action
    quantity: float = Field(
        description="Nombre d'actions. 0 si action = hold. Fractions (ex : 0.0288) autorisées seulement si "
        "allow_fractional est vrai dans les limites, sinon entier."
    )
    order_type: OrderType = Field(description="market ou limit. Pour hold, mettre market.")
    limit_price: float | None = Field(description="Prix limite si order_type = limit, sinon null.")
    confidence: float = Field(description="Confiance entre 0 et 1.")
    rationale: str = Field(description="Deux phrases maximum expliquant la décision.")


class TradePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_summary: str = Field(description="Lecture du marché en trois phrases maximum.")
    decisions: list[Decision] = Field(description="Une décision par symbole de l'univers, sans exception.")


# ---- Plan écrit par l'analyste (Claude en session) et exécuté par le bot ----

class AnalystOrder(BaseModel):
    """Un ordre décidé par l'analyste. Le bot en calcule la quantité et le prix au cours du moment."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    action: Literal["buy", "sell"]
    notional_usd: float | None = Field(default=None, description="Montant visé en USD (achat).")
    quantity: float | None = Field(default=None, description="Quantité précise ; vente sans quantité = toute la position.")
    order_type: Literal["limit", "market"] = "limit"
    limit_price: float | None = Field(default=None, description="Vide = dernier cours ± 0,2 %.")
    rationale: str

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _sizing(self) -> "AnalystOrder":
        if self.action == "buy" and not (self.notional_usd or self.quantity):
            raise ValueError(f"Achat {self.symbol} sans notional_usd ni quantity.")
        for name in ("notional_usd", "quantity", "limit_price"):
            v = getattr(self, name)
            if v is not None and v <= 0:
                raise ValueError(f"{name} doit être positif ({self.symbol}).")
        return self


class AnalystPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: datetime
    expires_at: datetime
    market_summary: str
    orders: list[AnalystOrder] = Field(default_factory=list)
    notes: str = ""

    @field_validator("created_at", "expires_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)

    @model_validator(mode="after")
    def _window(self) -> "AnalystPlan":
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at doit être postérieur à created_at.")
        return self
