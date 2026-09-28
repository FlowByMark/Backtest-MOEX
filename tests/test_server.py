import datetime as dt
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from backtest import server
from backtest.market_data import connect, store_day


class ServerTest(unittest.TestCase):
    def test_local_api_reopens_history_and_marks(self):
        with tempfile.TemporaryDirectory() as temp:
            old_db, old_marks, old_dir = server.DB_PATH, server.MARKS_PATH, server.DATA_DIR
            server.DATA_DIR = Path(temp)
            server.DB_PATH = Path(temp) / "market.sqlite3"
            server.MARKS_PATH = Path(temp) / "marks.json"
            try:
                with connect(server.DB_PATH) as db:
                    store_day(db, dt.date(2026, 9, 24), "Si", "SiZ6", [
                        {"begin": "2026-09-24 09:00:00", "end": "2026-09-24 09:01:00",
                         "open": 85000, "high": 85010, "low": 84990,
                         "close": 85005, "volume": 1}])
                http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                thread = threading.Thread(target=http.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{http.server_port}"
                try:
                    path = "/api/minutes?instrument=Si&from=2026-09-24T00:00:00&till=2026-09-25T00:00:00"
                    with urllib.request.urlopen(base + path) as response:
                        bars = json.load(response)
                    self.assertEqual((len(bars), bars[0]["contract"]), (1, "SiZ6"))
                    mark = [{"instrument": "Si", "price": 84990,
                             "created_at": "2026-09-24T09:01:00", "type": "level"}]
                    request = urllib.request.Request(base + "/api/marks",
                        data=json.dumps(mark).encode("utf-8"),
                        headers={"Content-Type": "application/json"}, method="POST")
                    with urllib.request.urlopen(request) as response:
                        self.assertEqual(json.load(response)["saved"], 1)
                    with urllib.request.urlopen(base + "/api/marks") as response:
                        self.assertEqual(json.load(response), mark)
                    self.assertEqual(json.loads(server.MARKS_PATH.read_text()), mark)
                finally:
                    http.shutdown()
                    http.server_close()
                    thread.join(timeout=2)
            finally:
                server.DB_PATH, server.MARKS_PATH, server.DATA_DIR = old_db, old_marks, old_dir


if __name__ == "__main__":
    unittest.main()
