"""Validation d'un plan de l'analyste, sans dépendre d'IBKR ni de l'API Claude.

    python3 -m claudetrader.plan orders/2026-11-03T1505.json

Vérifie le format, la fenêtre de validité et que chaque symbole est dans la liste
autorisée de settings.env. Code de retour 0 si le plan est valide.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .models import AnalystPlan


def allowed_symbols(settings_path: str = "settings.env") -> set[str]:
    for line in Path(settings_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("SYMBOLS="):
            return {s.strip().upper() for s in line.split("=", 1)[1].split(",") if s.strip()}
    return set()


def main(argv: list[str]) -> int:
    if not argv:
        print("Usage : python3 -m claudetrader.plan <fichier.json> [settings.env]", file=sys.stderr)
        return 2
    path = Path(argv[0])
    settings = argv[1] if len(argv) > 1 else "settings.env"
    try:
        plan = AnalystPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"PLAN INVALIDE ({path}) : {exc}", file=sys.stderr)
        return 1
    if plan.id != path.stem:
        print(f"PLAN INVALIDE : id '{plan.id}' différent du nom de fichier '{path.stem}'.", file=sys.stderr)
        return 1
    allowed = allowed_symbols(settings)
    outside = [o.symbol for o in plan.orders if o.action == "buy" and o.symbol not in allowed]
    if outside:
        print(f"PLAN INVALIDE : achat hors liste autorisée {outside} (voir SYMBOLS dans {settings}).", file=sys.stderr)
        return 1
    print(f"Plan valide : {plan.id}, {len(plan.orders)} ordre(s), valable jusqu'à {plan.expires_at.isoformat()}.")
    for o in plan.orders:
        size = f"{o.notional_usd:g} USD" if o.notional_usd else (f"x{o.quantity:g}" if o.quantity else "toute la position")
        print(f"  {o.action} {o.symbol} {size} {o.order_type}{f' @ {o.limit_price}' if o.limit_price else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
