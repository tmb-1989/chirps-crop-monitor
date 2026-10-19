"""Email alerts for board status changes (V3.4).

Two channels, both reading live.country_risk_log (written by
compute/country_risk.py) and both via the Migadu sender in
flood_signals._email (minibot@remote-brain.com, Keychain MIGADU_SMTP,
recipients pinned to ALERT_RECIPIENTS in code):

1. IMMEDIATE — any flood or hydropower light stepping yellow -> red
   sends at the daily run that detects it ("immediately" at the
   pipeline's cadence: the 07:30 cron). One email per run covering
   every such escalation, deduplicated through live.alert_state so a
   re-run the same day cannot re-send.
2. WEEKLY DIGEST — Monday mornings: every status change of the last
   7 days grouped by country, plus the current red lights, one email.
   live.alert_state stops a second send if the cron reruns.

Run by run_update.sh right after country_risk.py (non-fatal).
Usage: python compute/status_alerts.py [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
sys.path.insert(0, str(ROOT / "compute"))
import db  # noqa: E402
from flood_signals import _email  # noqa: E402

DOT = {"green": "🟢", "yellow": "🟡", "red": "🔴", "gray": "⚪"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS live.alert_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _get(con, key):
    r = con.execute("SELECT value FROM live.alert_state WHERE key=?",
                    (key,)).fetchone()
    return r[0] if r else None


def _set(con, key, value):
    con.execute("INSERT OR REPLACE INTO live.alert_state VALUES (?,?)",
                (key, value))


def immediate(con, now: dt.datetime, dry: bool) -> None:
    """Flood/hydro yellow -> red escalations since the last check."""
    last = _get(con, "last_red_check") or "1970-01-01T00:00:00"
    rows = con.execute(
        "SELECT changed_at, country, factor, prev, status, reason FROM "
        "live.country_risk_log WHERE changed_at > ? AND factor IN "
        "('flood', 'hydro') AND prev='yellow' AND status='red' "
        "ORDER BY country", (last,)).fetchall()
    _set(con, "last_red_check", now.isoformat(timespec="seconds"))
    if not rows:
        print("immediate: no flood/hydro yellow->red escalations")
        return
    lines = [f"{DOT['red']} {c} {f.upper()}: yellow -> RED\n   {r}"
             f"\n   (board change at {t})"
             for t, c, f, _p, _s, r in rows]
    subject = ("RED ALERT — " +
               ", ".join(sorted({f"{c} {f}" for _, c, f, *_ in rows})))
    body = ("The country risk board just escalated the following "
            "flood/hydropower lights from yellow to RED:\n\n"
            + "\n\n".join(lines) +
            "\n\nDashboard: Overview tab -> hover the light for detail."
            "\nThis alert fires at the pipeline's daily cadence; "
            "weekly digests cover all other changes.\n")
    print(f"immediate: {len(rows)} escalation(s) -> email")
    if dry:
        print(subject + "\n" + body)
    else:
        _email(subject, body)


def weekly(con, now: dt.datetime, dry: bool) -> None:
    """Monday digest: all changes of the last 7 days + current reds."""
    today = now.date()
    last = _get(con, "last_digest")
    last_d = dt.date.fromisoformat(last) if last else None
    # due on Mondays — but if a Monday send failed (locked keychain at
    # the 07:30 cron, 19 Oct 2026), retry on the following days until
    # one lands rather than staying silent for the whole week
    overdue = last_d is not None and (today - last_d).days > 7
    if today.weekday() != 0 and not overdue:
        print("weekly: not Monday — skipped")
        return
    if last_d == today:
        print("weekly: already sent today")
        return
    since = (now - dt.timedelta(days=7)).isoformat(timespec="seconds")
    rows = con.execute(
        "SELECT changed_at, country, factor, prev, status, reason FROM "
        "live.country_risk_log WHERE changed_at > ? "
        "ORDER BY country, changed_at", (since,)).fetchall()
    reds = con.execute(
        "SELECT country, factor, reason FROM live.country_risk "
        "WHERE status='red' ORDER BY country").fetchall()
    by_c: dict = {}
    for t, c, f, p, s, r in rows:
        by_c.setdefault(c, []).append(
            f"  {DOT.get(p, '⚪')}->{DOT.get(s, '⚪')} {f}: "
            f"{(r or '')[:120]}  [{t[:10]}]")
    parts = [f"{c}\n" + "\n".join(ls) for c, ls in sorted(by_c.items())]
    body = (f"El Niño Sovereign Risk Monitor — weekly digest "
            f"({(now - dt.timedelta(days=7)).date()} to {today})\n\n"
            f"STATUS CHANGES ({len(rows)}):\n\n"
            + ("\n\n".join(parts) if parts else "  none this week")
            + f"\n\nCURRENT RED LIGHTS ({len(reds)}):\n\n"
            + ("\n".join(f"  🔴 {c} {f}: {(r or '')[:120]}"
                         for c, f, r in reds) if reds else "  none")
            + "\n")
    subject = (f"Weekly risk digest — {len(rows)} change(s), "
               f"{len(reds)} red light(s)")
    print(f"weekly: digest with {len(rows)} changes, {len(reds)} reds")
    if dry:
        print(subject + "\n" + body)
    elif _email(subject, body):
        # only a completed send marks the week done — a skipped/failed
        # email retries at the next daily run
        _set(con, "last_digest", today.isoformat())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    con = db.connect()
    con.executescript(SCHEMA)
    now = dt.datetime.now(dt.timezone.utc)
    immediate(con, now, args.dry_run)
    weekly(con, now, args.dry_run)
    con.commit()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
