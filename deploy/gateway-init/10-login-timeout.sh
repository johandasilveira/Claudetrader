#!/usr/bin/env bash
# Exécuté dans le conteneur ib-gateway AVANT la génération de config.ini (X_SCRIPTS).
# Sur une petite machine (1 Go de RAM), IB Gateway met parfois plus d'une minute à afficher
# sa fenêtre de connexion ; IBC abandonne alors à 60 s et redémarre en boucle. On porte le
# délai à 600 s. Sans effet si le modèle n'est pas modifiable.
TMPL="${IBC_INI_TMPL:-/home/ibgateway/ibc/config.ini.tmpl}"
if [ -f "$TMPL" ]; then
  sed -i 's/^LoginDialogDisplayTimeout=.*/LoginDialogDisplayTimeout=600/' "$TMPL" \
    && echo ".> init-scripts: LoginDialogDisplayTimeout=600" \
    || echo ".> init-scripts: impossible de modifier $TMPL"
fi
