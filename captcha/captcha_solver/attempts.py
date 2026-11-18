"""Attempt log (SQLite) for success rates + the per-site budget guard."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

DB = Path(os.environ.get("CAPTCHA_STATE_DIR") or Path.home() / ".local/state/hommy-browsers/captcha") / "attempts.db"


def _con():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""create table if not exists attempts (
        ts real, site text, url text, kind text, input text, method text, outcome text,
        duration real, attempt int, detail text, screenshot text)""")
    return c


def record(site, url, kind, input, method, outcome, duration, attempt, detail=None, screenshot=None):
    with _con() as c:
        c.execute("insert into attempts values (?,?,?,?,?,?,?,?,?,?,?)",
                  (time.time(), site, url, kind, input, method, outcome, round(duration, 2), attempt,
                   json.dumps(detail or {}, default=str)[:4000], screenshot))


def recent(site, seconds) -> int:
    """Real solve attempts (not detections/waits) on this site in the last `seconds`."""
    with _con() as c:
        return c.execute("select count(*) from attempts where site=? and ts>? and attempt>0",
                         (site, time.time() - seconds)).fetchone()[0]


def stats():
    with _con() as c:
        return c.execute("""select site, kind, input, method, outcome, count(*), round(avg(duration),1),
                            max(datetime(ts,'unixepoch','localtime'))
                            from attempts group by 1,2,3,4,5 order by 1,2,3,4,5""").fetchall()
