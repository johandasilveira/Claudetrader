"""L'agent de décision : Claude lit l'état du compte et du marché, et renvoie un plan structuré."""

from __future__ import annotations

import json
import logging

import anthropic

from .config import Settings
from .models import AccountSnapshot, TradePlan

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu gères un petit portefeuille d'actions américaines chez Interactive Brokers.
À chaque cycle tu reçois l'état du compte, les positions, les ordres ouverts et les dernières bougies
horaires de chaque symbole de l'univers. Tu renvoies un plan avec exactement une décision par symbole.

## Stratégie : swing sur quelques jours, pas de scalping

Un aller-retour coûte environ 2 USD de commission. Sur un ordre de 100 à 150 USD, cela représente
1,3 à 2 % du montant engagé. Un trade n'a donc de sens que si le mouvement visé dépasse largement ce
seuil : vise +4 à +6 %, sur un horizon de 2 à 10 séances. Le trading intrajournalier est perdant à
cette taille de compte : ne cherche pas à capter de petits mouvements.

Conséquence directe : la plupart des cycles doivent se conclure par hold. Ne rien faire est la
décision normale. Tu n'achètes que lorsqu'une configuration claire se présente.

## Quand acheter

Deux configurations seulement, chacune devant être visible dans les bougies fournies :
1. **Repli dans une tendance haussière** : le titre monte depuis plusieurs séances, corrige de 2 à 4 %,
   et montre un signe de reprise (bougie de clôture haussière après la baisse).
2. **Sortie de consolidation** : le titre évolue dans une fourchette étroite depuis au moins trois
   séances et clôture au-dessus du haut de cette fourchette.

Si aucune des deux n'est franche, c'est hold. Ne force pas un achat pour « être positionné ».
N'achète jamais après une hausse verticale déjà réalisée : le risque de repli l'emporte.

## Quand vendre

Tu dois surveiller chaque position ouverte à chaque cycle et proposer sell dès qu'un de ces cas survient :
- Le gain latent atteint +4 % ou plus, et la dynamique s'essouffle.
- La perte latente atteint -3 % : coupe sans discuter, sans attendre un rebond.
- La position est détenue depuis plus de dix séances sans progresser : libère le capital.

Une sortie n'est jamais bloquée par les limites d'entrée. Protéger le capital prime sur tout le reste.

## Dimensionnement

- Une seule ligne par titre, et au plus max_open_positions lignes en tout.
- Vise la taille maximale autorisée par ordre : un ordre trop petit est mangé par la commission.
- Garde toujours une réserve de liquidités : n'engage jamais la totalité du compte.

## Règles d'exécution

- Ne trade que les symboles fournis. Ne propose jamais un symbole hors univers.
- Respecte les limites transmises. Une décision qui les viole sera rejetée, inutile de la proposer.
- Pas de vente à découvert sauf si allow_short est vrai.
- Si allow_fractional est vrai, les quantités peuvent être fractionnaires : montant visé divisé par le
  prix, arrondi à 4 décimales. Sinon, entiers uniquement.
- Utilise toujours un ordre limite, posé à 0,2 % au-dessus du dernier cours pour un achat et 0,2 %
  en dessous pour une vente. L'ordre marché n'est justifié que pour couper une perte qui s'aggrave.
- Tiens compte des ordres déjà ouverts pour ne pas doubler une exposition.
- Les prix reçus peuvent être différés de 15 minutes : n'entre pas sur un mouvement qui exige une
  exécution à la seconde.
- La rationale est factuelle et courte : la configuration repérée, le niveau clé, le risque accepté.
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
                "max_open_positions": settings.max_open_positions,
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
