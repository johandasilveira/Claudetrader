"""Calendrier du marché actions américain (NYSE / Nasdaq).

Sert à n'appeler Claude que pendant les heures de cotation : hors séance,
un cycle ne peut rien décider d'utile et coûte quand même un appel API.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

OPEN = time(9, 30)
CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Jours fériés boursiers américains (marché totalement fermé).
HOLIDAYS: set[date] = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}

# Séances écourtées : clôture à 13h00 heure de New York.
HALF_DAYS: set[date] = {
    date(2026, 11, 27), date(2026, 12, 24),
    date(2027, 11, 26),
}


def _close_time(day: date) -> time:
    return EARLY_CLOSE if day in HALF_DAYS else CLOSE


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in HOLIDAYS


def is_open(now: datetime | None = None) -> bool:
    """Le marché est-il ouvert à cet instant ?"""
    now_ny = (now or datetime.now(NY)).astimezone(NY)
    if not is_trading_day(now_ny.date()):
        return False
    return OPEN <= now_ny.timetz().replace(tzinfo=None) < _close_time(now_ny.date())


def next_open(now: datetime | None = None) -> datetime:
    """Prochaine ouverture, heure de New York."""
    now_ny = (now or datetime.now(NY)).astimezone(NY)
    day = now_ny.date()
    if is_trading_day(day) and now_ny.timetz().replace(tzinfo=None) < OPEN:
        return datetime.combine(day, OPEN, tzinfo=NY)
    for offset in range(1, 10):
        candidate = day + timedelta(days=offset)
        if is_trading_day(candidate):
            return datetime.combine(candidate, OPEN, tzinfo=NY)
    raise RuntimeError("Aucun jour de bourse trouvé dans les 10 prochains jours.")


def seconds_until_open(now: datetime | None = None) -> float:
    now_ny = (now or datetime.now(NY)).astimezone(NY)
    return max(0.0, (next_open(now_ny) - now_ny).total_seconds())


def minutes_since_open(now: datetime | None = None) -> float | None:
    """Minutes écoulées depuis l'ouverture, ou None si le marché est fermé."""
    now_ny = (now or datetime.now(NY)).astimezone(NY)
    if not is_open(now_ny):
        return None
    opened = datetime.combine(now_ny.date(), OPEN, tzinfo=NY)
    return (now_ny - opened).total_seconds() / 60
