#!/usr/bin/env bash
# Prépare une VM Debian/Ubuntu fraîche pour Claudetrader : swap 2 Go, Docker, clone du projet.
# À lancer UNE fois sur la machine, en une seule commande :
#   curl -fsSL https://raw.githubusercontent.com/johandasilveira/Claudetrader/claude/ibkr-bot/deploy/vm-setup.sh | sudo bash
set -euo pipefail

REPO="${REPO:-https://github.com/johandasilveira/Claudetrader}"
BRANCH="${BRANCH:-claude/ibkr-bot}"
TARGET="${TARGET:-/opt/claudetrader}"

echo "== 1/3 Fichier d'échange de 2 Go"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -q -w vm.swappiness=20
  grep -q vm.swappiness /etc/sysctl.conf || echo 'vm.swappiness=20' >> /etc/sysctl.conf
fi

echo "== 2/3 Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh >/dev/null
fi
REAL_USER="${SUDO_USER:-}"
if [[ -n "$REAL_USER" ]]; then usermod -aG docker "$REAL_USER"; fi

echo "== 3/3 Projet"
if ! command -v git >/dev/null; then apt-get install -y -q git >/dev/null; fi
if [[ ! -d "$TARGET" ]]; then
  git clone -q -b "$BRANCH" "$REPO" "$TARGET"
else
  git -C "$TARGET" pull -q
fi
if [[ -n "$REAL_USER" ]]; then chown -R "$REAL_USER":"$REAL_USER" "$TARGET"; fi
if [[ ! -f "$TARGET/.env" ]]; then
  cp "$TARGET/.env.example" "$TARGET/.env"
  grep -q '^JAVA_HEAP_SIZE=' "$TARGET/.env" || echo 'JAVA_HEAP_SIZE=512' >> "$TARGET/.env"
fi

free -h | sed -n 1,3p
cat <<MSG

Machine prête. Il reste à renseigner tes identifiants puis lancer le bot :

  nano $TARGET/.env          # TWS_USERID, TWS_PASSWORD, ANTHROPIC_API_KEY, garde-fous
  cd $TARGET && sudo docker compose up -d --build
  sudo docker compose logs -f   # approuve la notification IBKR Mobile sur ton téléphone

MSG
