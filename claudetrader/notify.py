"""Notifications sur le téléphone via ntfy (https://ntfy.sh), gratuit et sans compte.

Le nom du sujet (NTFY_TOPIC) fait office de mot de passe : quiconque le connaît
peut lire les messages. Il vit donc dans le .env local, jamais dans git.
Sans sujet configuré, toutes les notifications sont ignorées en silence.
"""

from __future__ import annotations

import logging
import urllib.request

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, topic: str = "", server: str = "https://ntfy.sh"):
        self.topic = topic.strip()
        self.server = server.rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.topic)

    def send(self, message: str, *, high: bool = False, tags: str = "chart_with_upwards_trend") -> None:
        """Envoie un message. N'échoue jamais : une notification ratée ne doit pas arrêter le bot."""
        if not self.enabled:
            return
        req = urllib.request.Request(
            f"{self.server}/{self.topic}",
            data=message.encode("utf-8"),
            method="POST",
            headers={
                "Title": "Claudetrader",
                "Tags": tags,
                "Priority": "high" if high else "default",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as exc:  # réseau, DNS, serveur ntfy indisponible
            log.warning("Notification non envoyée : %s", exc)
