"""The board (country_risk) and the CPI impulse (cpi_impulse) must accept
or reject the same WRSI reading on the same date (review item 1).

Run: ./venv/bin/python tests/test_wrsi_gate.py   (or pytest)
"""
import datetime as dt
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
sys.path.insert(0, str(ROOT / "compute"))

from config import WRSI_MIN_SEASON_DAYS  # noqa: E402
import country_risk  # noqa: E402
import cpi_impulse  # noqa: E402

SEASONS = "main:10-4"          # southern-Africa style, crosses the year
START = dt.date(2026, 10, 1)


def _db(reading: dt.date, value: float = 50.0):
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE zones (zone_key TEXT, iso3 TEXT, name TEXT,
                            seasons TEXT, sector TEXT);
        CREATE TABLE observations (zone_key TEXT, dataset TEXT,
                                   granule_start TEXT, value REAL);
        CREATE TABLE dekad_metrics (zone_key TEXT, granule_start TEXT,
                                    spi3 REAL);
    """)
    con.execute("INSERT INTO zones VALUES ('z','ZAF','Z',?,'staple')",
                (SEASONS,))
    con.execute("INSERT INTO observations VALUES "
                "('z','lwrsi_africa_dekad_pctm',?,?)",
                (reading.isoformat(), value))
    return con


def _both(reading: dt.date):
    con = _db(reading)
    today = reading + dt.timedelta(days=9)
    board = country_risk.drought_zones(con, today).iloc[0]
    cpi = cpi_impulse.current_wrsi(con, "z", SEASONS, today)
    return board, cpi


def test_modules_agree_across_the_gate():
    # every dekad start from the window opening to well past the gate
    for days in range(0, WRSI_MIN_SEASON_DAYS + 40):
        reading = START + dt.timedelta(days=days)
        if reading.day not in (1, 11, 21):
            continue
        board, cpi = _both(reading)
        expect = days >= WRSI_MIN_SEASON_DAYS
        assert bool(board.wrsi_in_season) == expect, (reading, board)
        assert (cpi is not None) == expect, (reading, cpi)
        # inside the window but before the gate: flagged early, no signal
        assert board.wrsi_early == int(not expect), (reading, board)
        if not expect:
            assert board.status != "red", (reading, board)


def test_counted_reading_drives_the_light():
    board, _ = _both(START + dt.timedelta(days=WRSI_MIN_SEASON_DAYS))
    assert board.status == "red" and board.reason == "WRSI 50"


if __name__ == "__main__":
    test_modules_agree_across_the_gate()
    test_counted_reading_drives_the_light()
    print("ok")
