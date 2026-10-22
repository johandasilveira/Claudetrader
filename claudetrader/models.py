"""Structures de données partagées entre le courtier, l'agent et les garde-fous."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    quantity: int = Field(description="Nombre d'actions. 0 si action = hold.")
    order_type: OrderType = Field(description="market ou limit. Pour hold, mettre market.")
    limit_price: float | None = Field(description="Prix limite si order_type = limit, sinon null.")
    confidence: float = Field(description="Confiance entre 0 et 1.")
    rationale: str = Field(description="Deux phrases maximum expliquant la décision.")


class TradePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_summary: str = Field(description="Lecture du marché en trois phrases maximum.")
    decisions: list[Decision] = Field(description="Une décision par symbole de l'univers, sans exception.")
