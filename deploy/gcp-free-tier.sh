#!/usr/bin/env bash
# Crée une VM Google Cloud "Always Free" (e2-micro) prête pour Claudetrader.
# À exécuter dans Google Cloud Shell (https://shell.cloud.google.com) ou avec gcloud installé.
#
#   bash deploy/gcp-free-tier.sh            # crée la VM
#   bash deploy/gcp-free-tier.sh ssh        # ouvre un terminal dessus
#
# Conditions du palier gratuit (à respecter, sinon facturation) :
#   - 1 seule e2-micro, dans us-west1, us-central1 ou us-east1
#   - disque "pd-standard" de 30 Go maximum
#   - 1 Go de sortie réseau par mois (le bot en consomme quelques Mo)
set -euo pipefail

VM_NAME="${VM_NAME:-claudetrader}"
ZONE="${ZONE:-us-central1-a}"
REPO="${REPO:-https://github.com/johandasilveira/Claudetrader}"
BRANCH="${BRANCH:-claude/ibkr-bot}"

if [[ "${1:-}" == "ssh" ]]; then
  exec gcloud compute ssh "$VM_NAME" --zone "$ZONE"
fi

PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
if [[ -z "$PROJECT" ]]; then
  echo "Aucun projet GCP sélectionné. Fais : gcloud config set project TON_PROJET" >&2
  exit 1
fi
echo "Projet : $PROJECT | VM : $VM_NAME | zone : $ZONE"

gcloud services enable compute.googleapis.com --quiet

STARTUP="$(mktemp)"
cat > "$STARTUP" <<STARTUP_EOF
#!/usr/bin/env bash
set -eux
# 1. Fichier d'échange de 2 Go : indispensable avec 1 Go de RAM pour IB Gateway.
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=20
fi
# 2. Docker
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
# 3. Le projet
if [[ ! -d /opt/claudetrader ]]; then
  apt-get install -y -q git
  git clone -b "$BRANCH" "$REPO" /opt/claudetrader
  chmod -R a+rwX /opt/claudetrader
fi
STARTUP_EOF

gcloud compute instances create "$VM_NAME" \
  --zone "$ZONE" \
  --machine-type e2-micro \
  --image-family debian-12 --image-project debian-cloud \
  --boot-disk-size 30GB --boot-disk-type pd-standard \
  --metadata-from-file startup-script="$STARTUP" \
  --quiet

rm -f "$STARTUP"

cat <<MSG

VM créée. Attends 2 à 3 minutes que l'installation se termine, puis :

  bash deploy/gcp-free-tier.sh ssh

Une fois connecté à la VM :

  cd /opt/claudetrader
  cp .env.example .env
  nano .env                       # TWS_USERID, TWS_PASSWORD, ANTHROPIC_API_KEY, garde-fous
  echo 'JAVA_HEAP_SIZE=512' >> .env
  sudo docker compose up -d --build
  sudo docker compose logs -f     # approuve la notification IBKR Mobile sur ton téléphone

MSG
