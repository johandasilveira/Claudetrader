#!/usr/bin/env bash
# Installe la mise à jour automatique du bot (à lancer une seule fois, avec sudo).
#   sudo bash deploy/install-auto-update.sh
# Pour la désactiver :  sudo systemctl disable --now claudetrader-update.timer
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"

cat > /etc/systemd/system/claudetrader-update.service <<UNIT
[Unit]
Description=Claudetrader : récupère la dernière version et redéploie le bot
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=DIR=$DIR
ExecStart=/usr/bin/bash $DIR/deploy/auto-update.sh
UNIT

cat > /etc/systemd/system/claudetrader-update.timer <<UNIT
[Unit]
Description=Claudetrader : vérifie les mises à jour toutes les 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now claudetrader-update.timer
# Premier passage immédiat, puis redéploiement du bot pour partir de la version courante.
systemctl start claudetrader-update.service || true
docker compose --project-directory "$DIR" up -d --build --no-deps bot
echo
echo "Mise à jour automatique active. Historique : journalctl -u claudetrader-update -n 20"
