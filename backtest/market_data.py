"""Minute history, durable storage and replay-safe candle aggregation."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TIMEFRAMES = {"M1", "M5", "M15", "H1", "H4", "D1", "W1", "MN1"}
INSTRUMENTS = {"Si": "Si", "CR": "CR"}

# The prototype's 2026 selection calendar. Verify expiry/session boundaries
# against exchange metadata before extending it to other years.
ROLLOVERS_2026 = (
    (dt.date(2026, 3, 19), "H6"),
    (dt.date(2026, 6, 18), "M6"),
    (dt.date(2026, 9, 17), "U6"),
    (dt.date(2026, 12, 17), "Z6"),
    (dt.date(2026, 12, 31), "H7"),
)


def contract_for(day: dt.date, instrument: str) -> str:
    if instrument not in INSTRUMENTS or day.year != 2026:
        raise ValueError("Only 2026 Si and CR contracts are configured")
    return instrument + next(code for last, code in ROLLOVERS_2026 if day <= last)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS minute_bars (
            contract TEXT NOT NULL, instrument TEXT NOT NULL,
            begin TEXT NOT NULL, end TEXT NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
            close REAL NOT NULL, volume REAL NOT NULL,
            PRIMARY KEY (contract, begin)
        );
        CREATE INDEX IF NOT EXISTS bars_by_instrument_time
            ON minute_bars(instrument, begin);
        CREATE TABLE IF NOT EXISTS download_days (
            instrument TEXT NOT NULL, contract TEXT NOT NULL, day TEXT NOT NULL,
            status TEXT NOT NULL, bar_count INTEGER NOT NULL,
            checked_at TEXT NOT NULL, error TEXT,
            PRIMARY KEY (instrument, contract, day)
        );
    """)
    return db


def request_day(day: dt.date, contract: str) -> list[dict]:
    """Fetch all ISS pages; fail on malformed or repeated pages."""
    result: list[dict] = []
    start = 0
    for _ in range(100):
        params = urllib.parse.urlencode({
            "from": day.isoformat(), "till": day.isoformat(), "interval": 1,
            "start": start, "iss.meta": "off", "iss.only": "candles",
            "candles.columns": "open,close,high,low,volume,begin,end",
        })
        url = ("https://iss.moex.com/iss/engines/futures/markets/forts/"
               f"boards/RFUD/securities/{contract}/candles.json?{params}")
        error = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Backtest-MOEX/1.0"})
                with urllib.request.urlopen(req, timeout=30) as response:
                    payload = json.load(response)
                break
            except Exception as exc:
                error = exc
                if attempt < 2:
                    time.sleep(attempt + 1)
        else:
            raise RuntimeError(f"{contract} {day}: {error}") from error
        block = payload.get("candles")
        if not isinstance(block, dict) or "columns" not in block or "data" not in block:
            raise ValueError(f"{contract} {day}: malformed ISS response")
        cols = block["columns"]
        if not set(("open", "high", "low", "close", "volume", "begin", "end")) <= set(cols):
            raise ValueError(f"{contract} {day}: missing candle columns")
        page = [dict(zip(cols, values)) for values in block["data"]]
        if not page:
            return result
        if len(page) != len({r["begin"] for r in page}):
            raise ValueError(f"{contract} {day}: duplicate timestamps in page")
        if result and page[0]["begin"] <= result[-1]["begin"]:
            raise ValueError(f"{contract} {day}: repeated or unordered ISS page")
        result.extend(page)
        start += len(page)
    raise RuntimeError(f"{contract} {day}: ISS pagination limit exceeded")


def store_day(db: sqlite3.Connection, day: dt.date, instrument: str,
              contract: str, rows: list[dict], error: str | None = None) -> None:
    normalized = []
    if error is None:
        for row in rows:
            begin = str(row["begin"]).replace(" ", "T")
            end = str(row["end"]).replace(" ", "T")
            o, h, l, c, v = (float(row[key]) for key in
                             ("open", "high", "low", "close", "volume"))
            if (dt.datetime.fromisoformat(begin).date() != day or
                    dt.datetime.fromisoformat(end) <= dt.datetime.fromisoformat(begin) or
                    l > min(o, c) or h < max(o, c) or v < 0):
                raise ValueError(f"{contract}: invalid bar {begin}")
            normalized.append((contract, instrument, begin, end, o, h, l, c, v))
        if len(normalized) != len({bar[2] for bar in normalized}):
            raise ValueError(f"{contract} {day}: duplicate bars")
    with db:
        if error is None:
            # Rewrite a mutable day atomically if it was downloaded before.
            db.execute("DELETE FROM minute_bars WHERE contract=? AND begin>=? AND begin<?",
                       (contract, day.isoformat(), (day + dt.timedelta(days=1)).isoformat()))
            db.executemany("INSERT INTO minute_bars VALUES (?,?,?,?,?,?,?,?,?)", normalized)
        db.execute("""INSERT INTO download_days VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(instrument, contract, day) DO UPDATE SET
              status=excluded.status, bar_count=excluded.bar_count,
              checked_at=excluded.checked_at, error=excluded.error""",
            (instrument, contract, day.isoformat(),
             "error" if error else "ready" if normalized else "empty",
             len(normalized), dt.datetime.now(dt.timezone.utc).isoformat(), error))


def download_range(db: sqlite3.Connection, first: dt.date, last: dt.date,
                   workers: int = 4, progress=print) -> dict:
    if first > last or first.year != 2026 or last.year != 2026:
        raise ValueError("Choose an ordered range inside 2026")
    # Moscow time is UTC+03:00 in the supported 2026 history. A fixed offset
    # keeps the standard-library-only Windows installation independent of the
    # optional IANA tzdata package (often absent in Microsoft Store Python).
    today_moscow = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).date()
    last = min(last, today_moscow)
    jobs = []
    day = first
    while day <= last:
        for instrument in INSTRUMENTS:
            contract = contract_for(day, instrument)
            state = db.execute("SELECT status FROM download_days WHERE instrument=? AND contract=? AND day=?",
                               (instrument, contract, day.isoformat())).fetchone()
            # An empty weekday can mean an unavailable contract/board. Retry it;
            # empty weekends are ordinary and may be cached.
            cached = state and (state["status"] == "ready" or
                                state["status"] == "empty" and day.weekday() >= 5)
            if not cached or day == today_moscow:
                jobs.append((day, instrument, contract))
        day += dt.timedelta(days=1)
    summary = {"requested": len(jobs), "ready": 0, "empty": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        futures = {pool.submit(request_day, day, contract): (day, instrument, contract)
                   for day, instrument, contract in jobs}
        for future in as_completed(futures):
            day, instrument, contract = futures[future]
            try:
                rows = future.result()
                store_day(db, day, instrument, contract, rows)
                status = "ready" if rows else "empty"
            except Exception as exc:
                store_day(db, day, instrument, contract, [], str(exc))
                status = "error"
            summary[status] += 1
            progress(f"{day} {contract}: {status} ({summary['ready'] + summary['empty'] + summary['error']}/{len(jobs)})")
    return summary


def coverage(db: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in db.execute(
        "SELECT instrument, contract, day, status, bar_count, error "
        "FROM download_days ORDER BY day, instrument")]


def minute_window(db: sqlite3.Connection, instrument: str,
                  first: str, last: str) -> list[dict]:
    if instrument not in INSTRUMENTS:
        raise ValueError("Unknown instrument")
    return [dict(row) for row in db.execute("""
        SELECT contract, instrument, begin, end, open, high, low, close, volume
        FROM minute_bars WHERE instrument=? AND begin>=? AND begin<? ORDER BY begin
    """, (instrument, first, last))]


def bucket(begin: str, timeframe: str) -> str:
    stamp = dt.datetime.fromisoformat(begin)
    if timeframe == "MN1":
        return f"{stamp.year:04d}-{stamp.month:02d}-01T00:00:00"
    if timeframe == "W1":
        monday = stamp.date() - dt.timedelta(days=stamp.weekday())
        return monday.isoformat() + "T00:00:00"
    if timeframe == "D1":
        return stamp.date().isoformat() + "T00:00:00"
    minutes = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240}[timeframe]
    minute_of_day = stamp.hour * 60 + stamp.minute
    rounded = (minute_of_day // minutes) * minutes
    return stamp.replace(hour=rounded // 60, minute=rounded % 60,
                         second=0, microsecond=0).isoformat(timespec="seconds")


def aggregate(rows: list[dict], timeframe: str, cutoff: str | None = None) -> list[dict]:
    """Return candles known at cutoff; never aggregate across contract rolls."""
    if timeframe not in TIMEFRAMES:
        raise ValueError("Unknown timeframe")
    bars = []
    for row in sorted(rows, key=lambda r: r["begin"]):
        if cutoff is not None and row["end"] > cutoff:
            continue
        key = (row["contract"], bucket(row["begin"], timeframe))
        if not bars or bars[-1]["_key"] != key:
            bars.append({"_key": key, "contract": row["contract"],
                         "begin": key[1], "end": row["end"],
                         "open": row["open"], "high": row["high"],
                         "low": row["low"], "close": row["close"],
                         "volume": row["volume"]})
        else:
            bar = bars[-1]
            bar["end"] = row["end"]
            bar["high"] = max(bar["high"], row["high"])
            bar["low"] = min(bar["low"], row["low"])
            bar["close"] = row["close"]
            bar["volume"] += row["volume"]
    for bar in bars:
        del bar["_key"]
    return bars
