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
TRADING_MODE=live          # et IB_PORT=4001 si IB Gateway tourne en local
DRY_RUN=false
LIVE_TRADING_CONFIRM=I_UNDERSTAND_THIS_TRADES_REAL_MONEY
```

Sans la phrase exacte, le bot refuse de démarrer en mode réel. Conseil : fais d'abord tourner quelques cycles en `DRY_RUN=true` sur le compte réel pour lire ce que Claude propose avant de laisser partir les ordres.

## Faire tourner le bot dans le cloud (24h/24)

Le bot a besoin d'IB Gateway allumé en permanence. La solution : un petit serveur VPS Linux (Hetzner, OVH, Scaleway, DigitalOcean, environ 5 à 10 euros par mois, 2 Go de RAM minimum) avec Docker. Le fichier `docker-compose.yml` lance deux conteneurs : IB Gateway avec connexion automatique (image `ghcr.io/gnzsnz/ib-gateway`, basée sur IBC) et le bot.

### Option gratuite : Google Cloud e2-micro (Always Free)

Google Cloud offre à vie une petite machine x86 (1 Go de RAM, 30 Go de disque) dans une région américaine. C'est juste pour IB Gateway, mais ça tient avec un fichier d'échange de 2 Go et une mémoire Java réduite à 512 Mo. Il faut un compte Google Cloud avec une carte bancaire enregistrée (exigée par Google, mais rien n'est facturé tant que tu restes dans les limites : une seule e2-micro, disque `pd-standard` de 30 Go, région us-west1 / us-central1 / us-east1).

Tout se fait depuis le navigateur, même sur téléphone, via Google Cloud Shell :

1. Crée un projet sur https://console.cloud.google.com et active la facturation.
2. Ouvre https://shell.cloud.google.com puis :
   ```bash
   git clone -b claude/ibkr-bot https://github.com/johandasilveira/Claudetrader
   cd Claudetrader
   gcloud config set project TON_PROJET
   bash deploy/gcp-free-tier.sh
   ```
3. Attends deux minutes, puis `bash deploy/gcp-free-tier.sh ssh` et suis les instructions affichées (copie de `.env`, `JAVA_HEAP_SIZE=512`, `docker compose up -d --build`).

Le script `deploy/gcp-free-tier.sh` crée la machine, le fichier d'échange, installe Docker et clone le projet. Le démarrage de la passerelle prend 2 à 4 minutes sur cette machine. Si la mémoire ne suffit pas malgré tout, la seule issue est une machine plus grande (e2-small, environ 12 € par mois) ou un VPS classique.

### Mise en place sur un VPS classique

```bash
# Sur le serveur, une seule fois
curl -fsSL https://get.docker.com | sh
git clone -b claude/ibkr-bot https://github.com/johandasilveira/Claudetrader
cd Claudetrader
cp .env.example .env
nano .env        # TWS_USERID, TWS_PASSWORD, ANTHROPIC_API_KEY, garde-fous

# Lancer
docker compose up -d --build
docker compose logs -f
```

Au premier démarrage, la passerelle se connecte à IBKR et **déclenche une notification IBKR Mobile sur ton téléphone**. Approuve-la dans les trois minutes. Le bot attend que la passerelle soit prête, puis démarre.

### Ce qu'il faut savoir sur l'authentification IBKR

- IBKR impose la double authentification. Grâce à `AUTO_RESTART_TIME`, le redémarrage quotidien de la passerelle ne redemande pas de validation.
- IBKR force malgré tout une **ré-authentification complète une fois par semaine**, le dimanche. Ce jour-là tu recevras une notification IBKR Mobile à approuver. Si tu ne réponds pas, la passerelle réessaie (`TWOFA_TIMEOUT_ACTION=restart`) et le bot reste en attente, sans trader, jusqu'à ce que tu valides.
- Si la passerelle redémarre, le bot le détecte et se reconnecte tout seul.

### Commandes utiles

```bash
docker compose logs -f bot                 # suivre le bot
docker compose exec bot python -m claudetrader status   # lire le compte
tail -f data/journal.jsonl                 # journal brut
docker compose restart ib-gateway          # forcer une reconnexion
docker compose down                        # tout arrêter
```

Pour voir la fenêtre de la passerelle (utile en cas de message bloquant), mets un mot de passe dans `VNC_SERVER_PASSWORD`, ouvre un tunnel `ssh -L 5900:127.0.0.1:5900 user@serveur` et connecte un client VNC sur `localhost:5900`.

### Sécurité du serveur

Les ports de la passerelle ne sont exposés que sur la boucle locale du serveur, jamais sur Internet. Garde le fichier `.env` hors de git (il l'est déjà via `.gitignore`), active un pare-feu qui n'ouvre que SSH, et utilise une clé SSH plutôt qu'un mot de passe.

## Deux fichiers de configuration

| Fichier | Contenu | Dans git ? |
|---|---|---|
| `settings.env` | Univers, cadence, garde-fous, `DRY_RUN`, modèle Claude, mode de la passerelle. Aucun secret. | Oui |
| `.env` | Identifiants IBKR, clé API Claude, `LIVE_TRADING_CONFIRM`, réglages propres à la machine. | Non |

Le `.env` a priorité sur `settings.env`. Pour modifier un réglage, édite `settings.env` (depuis Claude Code ou à la main), pousse, puis sur la machine :

```bash
cd /opt/claudetrader && git pull && sudo docker compose up -d bot
```

Si une variable existe dans les deux fichiers, c'est la valeur du `.env` qui gagne : garde-y uniquement les secrets.

## Stratégie

Le bot fait du **swing sur quelques jours**, pas du trading intrajournalier. La raison est
arithmétique : un aller-retour coûte environ 2 USD de commission, soit 1,3 à 2 % d'un ordre de
100 à 150 USD. Seul un mouvement de 4 à 6 % sur 2 à 10 séances justifie ce coût.

Claude n'achète que sur deux configurations : un repli de 2 à 4 % dans une tendance haussière avec
signe de reprise, ou une sortie de consolidation au-dessus d'une fourchette tenue depuis au moins
trois séances. Il vend au gain de +4 %, coupe à -3 %, ou libère le capital après dix séances sans
progression. Dans tous les autres cas, il ne fait rien : **hold est la décision normale**.

Le bot ne tourne que pendant la séance américaine, un cycle par heure, en laissant passer les
30 premières minutes après l'ouverture. Hors séance, aucun appel à Claude n'est émis.

## Notifications sur le téléphone

Le bot envoie ses propres notifications, signées « Claudetrader », via l'appli gratuite
[ntfy](https://ntfy.sh) (iOS et Android, sans compte) :

- **avant chaque demande IBKR Mobile** : quand la passerelle se reconnecte, le bot prévient qu'une
  validation va arriver. Une demande IBKR Mobile **sans** ce message juste avant ne vient pas du bot :
  refuse-la ;
- à chaque ordre passé, avec le titre, la quantité, le prix et la raison ;
- quand un ordre échoue, et quand le coupe-circuit de perte journalière se déclenche.

Mise en place : installe ntfy, abonne-toi à un sujet au nom long et impossible à deviner, mets ce nom
dans `NTFY_TOPIC` du `.env`, redémarre le bot, puis teste :

```bash
sudo docker compose run --rm bot notify-test
```

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
| `ALLOW_FRACTIONAL` | Autorise les fractions d'actions (permission IBKR "Fractional Shares" requise). Indispensable sous quelques centaines d'euros. |
| `MIN_ORDER_VALUE` | Montant minimal d'un ordre, pour ne pas multiplier les ordres mangés par la commission minimale d'environ 1 $. |
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

## Skills Claude d'analyse (day-trader-fr)

Le dossier `.claude/skills/` contient les 11 skills du projet open source [dan-ai-lab/day-trader-fr](https://github.com/dan-ai-lab/day-trader-fr) (licence MIT, adaptation française de [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)). Ils s'utilisent dans Claude Code ouvert sur ce dépôt, en tapant par exemple `/trader-scanner` ou `/trader-timemachine TSLA`.

| Skill | Rôle |
|---|---|
| `/trader-scanner` | Repère les graphiques qui matchent une règle (cassure d'ouverture, drapeau haussier, survente, rebond). |
| `/trader-news` | Actualités, dépôts SEC et mouvements des titres suivis. |
| `/trader-entry` | Attend la clôture d'une bougie pour confirmer un déclencheur d'entrée. |
| `/trader-exit` | Fixe stop, objectif et heure de sortie avant l'achat. |
| `/trader-timemachine` | Simule 10 000 fins de séance et compare aux séances historiques. |
| `/trader-budget` | Calcule la taille de position à partir du budget et du risque maximal. |
| `/trader-risk` | Vérifie chaque proposition et la bloque si elle viole tes règles. |
| `/trader-broker` | Prépare un ticket d'ordre, en simulation par défaut. |
| `/trader-alerts` | Alertes sur un prix, un stop, un objectif ou l'heure de clôture. |
| `/trader-analyst` | Lit les résultats publiés à la SEC et explique ce qui a changé. |
| `/trader-pelosi` | Lit les déclarations de transactions des membres du Congrès américain. |

Ces skills sont des outils d'**analyse manuelle et de simulation** : données Yahoo gratuites et différées, marchés américains uniquement, aucun ordre réel envoyé par les scripts. Ils complètent le bot autonome mais n'en font pas partie. Les scripts n'ont besoin que de Python 3.8+, sans bibliothèque externe.
