"""Journal JSONL : chaque cycle, chaque décision et chaque ordre y sont consignés."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


class Journal:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, payload: dict[str, Any]) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, **payload}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def orders_today(self, today: date | None = None) -> int:
        """Nombre d'ordres réellement envoyés aujourd'hui (survit aux redémarrages)."""
        today = today or datetime.now(timezone.utc).date()
        if not self.path.exists():
            return 0
        count = 0
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") == "order" and rec.get("ts", "").startswith(today.isoformat()):
                    count += 1
        return count
