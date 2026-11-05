# Plans de l'analyste

Chaque fichier `AAAA-MM-JJTHHMM.json` (heure UTC) est un plan écrit par Claude lors d'une session
d'analyse planifiée. Le bot le récupère grâce à la mise à jour automatique, l'exécute **une seule fois**
s'il est encore valable, puis l'ignore pour toujours. Les plans sans ordre servent de trace de l'analyse.

```json
{
  "id": "2026-11-03T1505",
  "created_at": "2026-11-03T15:05:00Z",
  "expires_at": "2026-11-03T16:35:00Z",
  "market_summary": "Lecture du marché en quelques phrases.",
  "orders": [
    {"symbol": "WMT", "action": "buy", "quantity": 1, "order_type": "limit",
     "rationale": "Repli de 3 % dans une tendance haussière, clôture de reprise hier."},
    {"symbol": "NVDA", "action": "sell", "rationale": "Objectif atteint, dynamique qui s'essouffle."}
  ],
  "notes": "Ce qui a été vérifié, les candidats écartés et pourquoi."
}
```

- `quantity` : nombre **entier** d'actions (IBKR refuse les fractions par l'API). Une vente sans
  quantité vend toute la position.
- `notional_usd` : alternative ; le bot en déduit un nombre entier d'actions, arrondi à l'inférieur.
- `limit_price` : facultatif ; par défaut, dernier cours plus 0,2 % à l'achat, moins 0,2 % à la vente.
- Chaque ordre passe ensuite par les garde-fous du bot (`settings.env`), qui peuvent le refuser.

Vérification avant publication : `python3 -m claudetrader.plan orders/<fichier>.json`
