# Claudetrader

Bot de trading autonome : **Claude décide, Interactive Brokers exécute, des garde-fous codés en dur encadrent.**

```
IB Gateway (papier ou réel)
        │  ib_async (API TWS)
        ▼
  broker.py  ──snapshot──▶  agent.py (Claude, sortie structurée)
                                   │ plan : une décision par symbole
                                   ▼
                              risk.py  (garde-fous)
                                   │ ordres approuvés
                                   ▼
                       broker.py.place_order()  ──▶  IBKR
                                   │
                              journal.jsonl
```

À chaque cycle le bot lit le compte (valeur, liquidités, PnL du jour, positions, ordres ouverts) et les dernières bougies de chaque symbole, envoie le tout à Claude, reçoit un plan structuré, passe chaque décision dans les garde-fous, et n'envoie à IBKR que ce qui est approuvé.

## Prérequis

1. **Un compte Interactive Brokers** avec un sous-compte papier (simulation). Tout nouveau compte en a un.
2. **IB Gateway** installé et lancé sur la machine qui fera tourner le bot : https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
   Dans IB Gateway : *Configure → Settings → API → Settings* :
   - cocher *Enable ActiveX and Socket Clients*
   - décocher *Read-Only API*
   - vérifier le port : **4002** en papier, 4001 en réel
   - ajouter `127.0.0.1` aux *Trusted IPs*
3. **Une clé API Anthropic** : https://console.anthropic.com/
4. **Python 3.10 ou plus récent.**

## Installation

```bash
git clone <ce dépôt> && cd Claudetrader
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Ouvre `.env` et renseigne au minimum `ANTHROPIC_API_KEY`. Les autres valeurs par défaut sont volontairement prudentes : port papier, `DRY_RUN=true`, petits montants.

## Utilisation

```bash
# 1. Vérifier la connexion à IB Gateway et lire le compte (aucun appel à Claude)
python -m claudetrader status

# 2. Un seul cycle de décision, sans envoyer d'ordre (DRY_RUN=true par défaut)
python -m claudetrader once

# 3. Boucle continue, un cycle toutes les INTERVAL_MINUTES
python -m claudetrader run
```

Tout est consigné dans `data/journal.jsonl` : instantanés de compte, plans de Claude, verdicts des garde-fous, ordres envoyés, erreurs.

## Passer du dry-run aux ordres réels (en compte papier)

Quand les plans journalisés te paraissent sensés, mets `DRY_RUN=false` dans `.env`. Les ordres approuvés partent alors sur le compte **papier** (port 4002). Laisse tourner plusieurs semaines avant d'envisager le réel.

## Passer en compte réel

Ce n'est pas une simple option. Il faut **trois** changements délibérés dans `.env` :

```
IB_PORT=4001
DRY_RUN=false
LIVE_TRADING_CONFIRM=I_UNDERSTAND_THIS_TRADES_REAL_MONEY
```

Sans la phrase exacte, le bot refuse de démarrer sur un port réel. Il faut aussi relancer IB Gateway en mode *Live*.

## Garde-fous

Tous dans `.env`, tous vérifiés dans `claudetrader/risk.py` avant chaque ordre :

| Variable | Rôle |
|---|---|
| `SYMBOLS` | Seuls ces symboles peuvent être tradés. Tout autre est rejeté. |
| `MAX_ORDER_VALUE` | Montant maximal d'un ordre. |
| `MAX_POSITION_VALUE` | Valeur maximale d'une position, ordres ouverts compris. |
| `MAX_DAILY_LOSS` | Si le PnL du jour descend sous ce seuil, plus aucun achat (les ventes restent possibles). |
| `MAX_ORDERS_PER_DAY` | Plafond d'ordres envoyés par jour, compté depuis le journal, donc résistant aux redémarrages. |
| `MAX_LIMIT_DEVIATION_PCT` | Un prix limite trop éloigné du dernier cours est rejeté. |
| `ALLOW_SHORT` | Vente à découvert interdite par défaut. |
| `DRY_RUN` | En `true`, connexion IBKR en lecture seule : la passerelle elle-même refuserait un ordre. |

En plus de ces règles, le bot ne trade jamais si Claude refuse de répondre, si la réponse est tronquée ou invalide, ou si aucun prix n'est disponible pour le symbole.

## Coûts

- **IBKR** : aucune commission tant que tu n'envoies pas d'ordres réels. Les données de marché utilisées sont les cours différés de 15 minutes, gratuits.
- **Claude** : un cycle coûte quelques centimes avec `claude-opus-5-5` (le prompt système est mis en cache). Avec un cycle toutes les 30 minutes pendant les heures de marché, compte environ 15 cycles par jour.

## Tests

```bash
pytest
```

Les tests couvrent les garde-fous, la configuration (verrou du mode réel), le journal, l'agent (avec un client Claude simulé) et la boucle (avec un courtier simulé). Ils ne nécessitent ni IB Gateway ni clé API.

## Limites connues

- Le bot ne connaît pas les horaires de marché : hors séance, Claude reçoit les dernières bougies disponibles et décide en général `hold`. Les ordres `DAY` envoyés hors séance restent en attente jusqu'à l'ouverture.
- Les cours sont différés de 15 minutes sans abonnement aux données temps réel.
- IB Gateway demande une reconnexion quotidienne. Prévois un redémarrage automatique (IBC ou un conteneur type `ib-gateway-docker`) pour un fonctionnement sans surveillance.
- Ce projet est un point de départ, pas un conseil en investissement. Tu es seul responsable des ordres envoyés sur ton compte.
