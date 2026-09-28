import datetime as dt
import tempfile
import unittest
from pathlib import Path

from backtest.market_data import (aggregate, connect, contract_for,
                                  coverage, minute_window, store_day)


def row(begin, end, price, high=None, low=None, contract="SiZ6"):
    return {"contract": contract, "begin": begin, "end": end,
            "open": price, "high": high if high is not None else price,
            "low": low if low is not None else price, "close": price,
            "volume": 1}


class MarketDataTest(unittest.TestCase):
    def test_contract_boundary(self):
        self.assertEqual(contract_for(dt.date(2026, 9, 17), "Si"), "SiU6")
        self.assertEqual(contract_for(dt.date(2026, 9, 18), "CR"), "CRZ6")
        with self.assertRaises(ValueError):
            contract_for(dt.date(2027, 1, 1), "Si")

    def test_replay_cutoff_does_not_reveal_future_high(self):
        rows = [row("2026-09-24T09:00:00", "2026-09-24T09:01:00", 100),
                row("2026-09-24T09:01:00", "2026-09-24T09:02:00", 101),
                row("2026-09-24T09:02:00", "2026-09-24T09:03:00", 99, high=150)]
        known = aggregate(rows, "H1", "2026-09-24T09:02:00")
        self.assertEqual(len(known), 1)
        self.assertEqual((known[0]["high"], known[0]["close"]), (101, 101))
        self.assertEqual(aggregate(rows, "H1")[-1]["high"], 150)
        self.assertEqual(len(aggregate(rows, "M1", "2026-09-24T09:02:00")), 2)

    def test_roll_never_merges_different_contracts(self):
        rows = [row("2026-09-18T09:00:00", "2026-09-18T09:01:00", 100, contract="SiU6"),
                row("2026-09-18T09:01:00", "2026-09-18T09:02:00", 110, contract="SiZ6")]
        result = aggregate(rows, "H1")
        self.assertEqual([r["contract"] for r in result], ["SiU6", "SiZ6"])

    def test_week_and_month_boundaries(self):
        rows = [row("2026-08-30T23:00:00", "2026-08-30T23:01:00", 100),
                row("2026-08-31T09:00:00", "2026-08-31T09:01:00", 101),
                row("2026-09-01T09:00:00", "2026-09-01T09:01:00", 102)]
        self.assertEqual(len(aggregate(rows, "W1")), 2)
        self.assertEqual(len(aggregate(rows, "MN1")), 2)

    def test_store_and_query_across_days(self):
        with tempfile.TemporaryDirectory() as temp:
            with connect(Path(temp) / "market.sqlite3") as db:
                first = dt.date(2026, 9, 23)
                second = dt.date(2026, 9, 24)
                store_day(db, first, "Si", "SiZ6", [
                    {"begin": "2026-09-23 18:49:00", "end": "2026-09-23 18:50:00",
                     "open": 100, "high": 105, "low": 98, "close": 101, "volume": 2}])
                store_day(db, second, "Si", "SiZ6", [
                    {"begin": "2026-09-24 09:00:00", "end": "2026-09-24 09:01:00",
                     "open": 101, "high": 102, "low": 99, "close": 100, "volume": 3}])
                bars = minute_window(db, "Si", "2026-09-23T00:00:00", "2026-09-25T00:00:00")
                self.assertEqual(len(bars), 2)
                self.assertEqual(bars[0]["high"], 105)
                self.assertEqual(len(aggregate(bars, "D1")), 2)
                self.assertEqual([r["status"] for r in coverage(db)], ["ready", "ready"])

    def test_rejects_invalid_data_without_lying_about_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            with connect(Path(temp) / "market.sqlite3") as db:
                day = dt.date(2026, 9, 24)
                bad = {"begin": "2026-09-24 09:00:00", "end": "2026-09-24 09:01:00",
                       "open": 100, "high": 90, "low": 80, "close": 100, "volume": 1}
                with self.assertRaises(ValueError):
                    store_day(db, day, "Si", "SiZ6", [bad])
                self.assertEqual(coverage(db), [])


if __name__ == "__main__":
    unittest.main()
