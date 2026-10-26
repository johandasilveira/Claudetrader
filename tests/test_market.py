from datetime import datetime
from zoneinfo import ZoneInfo

from claudetrader import market

NY = ZoneInfo("America/New_York")
PARIS = ZoneInfo("Europe/Paris")


def ny(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=NY)


def test_open_during_regular_session():
    assert market.is_open(ny(2026, 10, 27, 10, 0))      # mardi 10h00 NY
    assert market.is_open(ny(2026, 10, 27, 9, 30))      # pile à l'ouverture
    assert not market.is_open(ny(2026, 10, 27, 9, 29))  # une minute avant
    assert not market.is_open(ny(2026, 10, 27, 16, 0))  # pile à la clôture


def test_closed_on_weekend_and_holiday():
    assert not market.is_open(ny(2026, 10, 24, 12, 0))   # samedi
    assert not market.is_open(ny(2026, 10, 25, 12, 0))   # dimanche
    assert market.is_open(ny(2026, 10, 23, 12, 0))       # vendredi : ouvert
    assert not market.is_open(ny(2026, 12, 25, 12, 0))   # Noël
    assert not market.is_open(ny(2026, 11, 26, 12, 0))   # Thanksgiving


def test_half_day_closes_early():
    assert market.is_open(ny(2026, 11, 27, 12, 30))      # lendemain de Thanksgiving, avant 13h
    assert not market.is_open(ny(2026, 11, 27, 13, 30))


def test_next_open_skips_weekend():
    # vendredi 17h NY -> lundi 9h30
    nxt = market.next_open(ny(2026, 10, 23, 17, 0))
    assert nxt == ny(2026, 10, 26, 9, 30)


def test_next_open_same_day_before_bell():
    nxt = market.next_open(ny(2026, 10, 27, 7, 0))
    assert nxt == ny(2026, 10, 27, 9, 30)


def test_seconds_until_open_is_zero_or_positive():
    assert market.seconds_until_open(ny(2026, 10, 27, 8, 30)) == 3600.0


def test_minutes_since_open():
    assert market.minutes_since_open(ny(2026, 10, 27, 10, 0)) == 30.0
    assert market.minutes_since_open(ny(2026, 10, 25, 10, 0)) is None


def test_paris_timezone_is_converted():
    # 15h30 à Paris fin octobre = 10h30 à New York, marché ouvert
    assert market.is_open(datetime(2026, 10, 27, 15, 30, tzinfo=PARIS))
