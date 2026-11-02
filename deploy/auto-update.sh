#!/usr/bin/env bash
# Récupère la dernière version du dépôt et redéploie le bot si elle a changé.
# Lancé toutes les 5 minutes par le minuteur systemd claudetrader-update.timer.
#
# Ne redéploie QUE le conteneur du bot (--no-deps) : la passerelle IBKR n'est jamais
# redémarrée ici, donc aucune demande IBKR Mobile n'est déclenchée par une mise à jour.
# Une modification de la configuration de la passerelle reste à appliquer à la main.
set -euo pipefail

DIR="${DIR:-/opt/claudetrader}"
BRANCH="${BRANCH:-claude/ibkr-bot}"
OWNER="$(stat -c %U "$DIR")"
cd "$DIR"

as_owner() { sudo -u "$OWNER" -H "$@"; }

as_owner git fetch -q origin "$BRANCH"
LOCAL="$(as_owner git rev-parse HEAD)"
REMOTE="$(as_owner git rev-parse "origin/$BRANCH")"
[ "$LOCAL" = "$REMOTE" ] && exit 0

echo "Nouvelle version : ${LOCAL:0:7} -> ${REMOTE:0:7}"
CHANGED="$(as_owner git diff --name-only "$LOCAL" "$REMOTE")"
if ! as_owner git merge --ff-only -q "origin/$BRANCH"; then
  echo "Mise à jour impossible en avance rapide (modification locale ?) : rien n'est déployé." >&2
  exit 1
fi
# Les plans de l'analyste (orders/) et ses notes (analyst/) sont lus directement par le bot via le
# dossier monté : inutile de reconstruire ni de redémarrer quoi que ce soit.
if ! printf '%s\n' "$CHANGED" | grep -qvE '^(orders|analyst)/'; then
  echo "Seuls des plans ou des notes d'analyse ont changé : le bot les lira à son prochain cycle."
  exit 0
fi
docker compose up -d --build --no-deps bot
echo "Bot redéployé : $(as_owner git log -1 --format='%h %s')"
