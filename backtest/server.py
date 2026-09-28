"""Local read-only market API plus durable drawing storage."""

from __future__ import annotations

import argparse
import datetime as dt
import http.server
import json
import sqlite3
import threading
import urllib.parse
import webbrowser
from pathlib import Path

from .market_data import connect, coverage, download_range, minute_window

DATA_DIR = Path(__file__).resolve().parent.parent / "backtest_data"
DB_PATH = DATA_DIR / "market.sqlite3"
MARKS_PATH = DATA_DIR / "marks.json"
WEB_DIR = Path(__file__).resolve().parent / "web"


class Handler(http.server.BaseHTTPRequestHandler):
    def send_json(self, value, code=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        try:
            if url.path == "/":
                body = (WEB_DIR / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif url.path == "/api/coverage":
                with connect(DB_PATH) as db:
                    self.send_json(coverage(db))
            elif url.path == "/api/marks":
                self.send_json(json.loads(MARKS_PATH.read_text(encoding="utf-8"))
                               if MARKS_PATH.exists() else [])
            elif url.path == "/api/minutes":
                instrument = query.get("instrument", [""])[0]
                first = query.get("from", [""])[0]
                last = query.get("till", [""])[0]
                left, right = dt.datetime.fromisoformat(first), dt.datetime.fromisoformat(last)
                if right <= left or right - left > dt.timedelta(days=31):
                    raise ValueError("Choose at most 31 days per view")
                with connect(DB_PATH) as db:
                    self.send_json(minute_window(db, instrument, first, last))
            else:
                self.send_error(404)
        except (ValueError, sqlite3.Error, OSError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_POST(self):
        if self.path != "/api/marks":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 2 or size > 2_000_000:
                raise ValueError("Invalid mark payload size")
            data = json.loads(self.rfile.read(size))
            if (not isinstance(data, list) or len(data) > 10000 or
                    any(not isinstance(mark, dict) or
                        mark.get("instrument") not in {"Si", "CR"} or
                        not isinstance(mark.get("price"), (int, float)) or
                        not isinstance(mark.get("created_at"), str)
                        for mark in data)):
                raise ValueError("Invalid marks")
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = MARKS_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(MARKS_PATH)
            self.send_json({"saved": len(data)})
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def log_message(self, fmt, *args):
        if args and str(args[1]) not in {"200", "304"}:
            super().log_message(fmt, *args)


def main():
    parser = argparse.ArgumentParser(description="Backtest MOEX — new platform preview")
    sub = parser.add_subparsers(dest="command", required=True)
    download = sub.add_parser("download", help="Download continuous Si and CR M1 history")
    download.add_argument("--from", dest="first", required=True, type=dt.date.fromisoformat)
    download.add_argument("--till", dest="last", required=True, type=dt.date.fromisoformat)
    download.add_argument("--workers", type=int, default=4)
    sub.add_parser("serve", help="Open local chart")
    sub.add_parser("coverage", help="Show missing/error days")
    args = parser.parse_args()
    if args.command == "download":
        with connect(DB_PATH) as db:
            print(json.dumps(download_range(db, args.first, args.last, args.workers),
                             ensure_ascii=False))
    elif args.command == "coverage":
        with connect(DB_PATH) as db:
            for row in coverage(db):
                if row["status"] != "ready":
                    print(f'{row["day"]} {row["contract"]}: {row["status"]} {row["error"] or ""}')
    else:
        with connect(DB_PATH):
            pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Backtest MOEX: {url}", flush=True)
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()


if __name__ == "__main__":
    main()
