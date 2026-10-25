"""L'agent de décision : Claude lit l'état du compte et du marché, et renvoie un plan structuré."""

from __future__ import annotations

import json
import logging

import anthropic

from .config import Settings
from .models import AccountSnapshot, TradePlan

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu es le gestionnaire d'un petit portefeuille d'actions chez Interactive Brokers.
À chaque cycle tu reçois l'état du compte, les positions, les ordres ouverts et les dernières bougies
de chaque symbole de l'univers. Tu renvoies un plan avec exactement une décision par symbole.

Principes :
- Le capital est petit et la préservation prime. En cas de doute, hold.
- Ne trade que les symboles fournis. Ne propose jamais un symbole hors univers.
- Respecte les limites : montant maximal par ordre, valeur maximale par position, perte maximale par jour.
  Une décision qui les viole sera rejetée par les garde-fous, inutile de la proposer.
- Pas de vente à découvert sauf si explicitement autorisé.
- Si allow_fractional est vrai, les quantités peuvent être fractionnaires (ex : 0.0288 action) : calcule-les
  à partir du montant que tu veux engager divisé par le prix, arrondi à 4 décimales. Sinon, entiers uniquement.
- Chaque ordre coûte environ 1 USD de commission minimale : sur un petit compte, ne trade que si le gain
  espéré dépasse nettement ce coût, et n'enchaîne pas les petits ordres.
- Préfère les ordres limite proches du dernier cours. Un ordre marché n'est justifié qu'en cas d'urgence.
- Tiens compte des ordres déjà ouverts pour ne pas doubler une exposition.
- Les données de prix peuvent être différées de 15 minutes.
- La rationale doit être factuelle et courte.
"""


class ClaudeAgent:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None):
        self.s = settings
        self.client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key or None)

    @staticmethod
    def build_user_message(snap: AccountSnapshot, settings: Settings, orders_today: int) -> str:
        market = []
        for m in snap.market:
            market.append(
                {
                    "symbol": m.symbol,
                    "last_price": m.last_price,
                    "bars": [b.model_dump() for b in m.bars[-40:]],
                }
            )
        payload = {
            "timestamp": snap.timestamp,
            "account": {
                "currency": snap.currency,
                "net_liquidation": snap.net_liquidation,
                "available_funds": snap.available_funds,
                "daily_pnl": snap.daily_pnl,
            },
            "limits": {
                "max_order_value": settings.max_order_value,
                "max_position_value": settings.max_position_value,
                "max_daily_loss": settings.max_daily_loss,
                "max_orders_per_day": settings.max_orders_per_day,
                "orders_already_sent_today": orders_today,
                "max_limit_deviation_pct": settings.max_limit_deviation_pct,
                "allow_short": settings.allow_short,
                "allow_fractional": settings.allow_fractional,
                "min_order_value": settings.min_order_value,
            },
            "universe": settings.symbols,
            "positions": [p.model_dump() for p in snap.positions],
            "open_orders": [o.model_dump() for o in snap.open_orders],
            "market": market,
        }
        return (
            "État courant du compte et du marché (JSON) :\n"
            + json.dumps(payload, ensure_ascii=False, indent=1)
            + "\n\nRenvoie le plan de trading pour ce cycle."
        )

    def decide(self, snap: AccountSnapshot, orders_today: int) -> TradePlan:
        user_message = self.build_user_message(snap, self.s, orders_today)
        response = self.client.beta.messages.parse(
            model=self.s.claude_model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": self.s.claude_effort},
            output_format=TradePlan,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message}],
        )
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise RuntimeError(f"Claude a refusé la requête ({details}). Aucun ordre ce cycle.")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Réponse de Claude tronquée (max_tokens). Aucun ordre ce cycle.")
        plan = response.parsed_output
        if plan is None:
            raise RuntimeError("Claude n'a pas renvoyé de plan exploitable.")
        usage = response.usage
        log.info(
            "Claude: %d tokens entrée (%d en cache), %d sortie",
            usage.input_tokens, usage.cache_read_input_tokens or 0, usage.output_tokens,
        )
        return plan
