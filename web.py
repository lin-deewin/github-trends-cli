#!/usr/bin/env python3
"""Local web dashboard for GitHub Trends CLI. Python standard library only."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import ghtrend

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
PERIODS = {"3d": 3, "7d": 7, "1m": 30, "3m": 90}
REFRESH_LOCK = threading.Lock()
REFRESH_STATE = {
    "running": False, "started_at": None, "finished_at": None,
    "returncode": None, "output": "",
}


def period_days(value: str) -> int:
    try:
        return PERIODS[value]
    except KeyError as exc:
        raise ValueError("period must be one of: 3d, 7d, 1m, 3m") from exc


def _repo_metadata(con, full_name: str):
    return con.execute("""
        SELECT r.*, h.start_day AS history_start, h.end_day AS history_end,
               h.fetched_at AS history_fetched_at
        FROM repos r LEFT JOIN history_status h ON h.repo_id=r.id
        WHERE r.full_name=? COLLATE NOCASE
    """, (full_name,)).fetchone()


def build_dashboard(con, period="7d", *, search="", category="", language="", limit=100, today=None):
    days = period_days(period)
    today = today or ghtrend.utc_day()
    rows, missing = ghtrend.ranked_rows(con, days, source="history", today=today)
    categories = sorted({row["category"] for row in rows if row["category"] != "未分类"})
    languages = sorted({row["language"] for row in rows if row["language"] not in ("-", "")})
    q = search.strip().casefold()
    filtered = []
    for row in rows:
        if category and row["category"] != category:
            continue
        if language and row["language"].casefold() != language.casefold():
            continue
        if q:
            haystack = " ".join((row["repo"], row["description"], row["category"],
                                 row["language"], " ".join(row["topics"]))).casefold()
            if q not in haystack:
                continue
        item = dict(row)
        item["average_daily"] = round(item["gain"] / days, 1)
        filtered.append(item)
    filtered = filtered[:max(1, min(int(limit), 500))]

    # Real seven-day mini-trend data for the table; never fabricate a sparkline.
    spark_start = today - timedelta(days=7)
    metadata = {row["full_name"]: (row["id"], date.fromisoformat(row["created_at"][:10]))
                for row in con.execute("SELECT id,full_name,created_at FROM repos")}
    for item in filtered:
        repo_id, created = metadata[item["repo"]]
        daily_rows = con.execute(
            "SELECT day,count FROM star_days WHERE repo_id=? AND day>=? AND day<?",
            (repo_id, spark_start.isoformat(), today.isoformat()),
        ).fetchall()
        daily_map = {r["day"]: int(r["count"]) for r in daily_rows}
        item["sparkline"] = [
            (daily_map.get((spark_start + timedelta(days=i)).isoformat())
             if (spark_start + timedelta(days=i)).isoformat() in daily_map
             else (0 if spark_start + timedelta(days=i) < created else None))
            for i in range(7)
        ]

    total_growth = sum(row["gain"] for row in rows)
    total_repos = con.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
    latest = con.execute("SELECT MAX(observed_at) FROM repos").fetchone()[0]
    return {
        "period": period,
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_through": (today - timedelta(days=1)).isoformat(),
        "summary": {
            "monitored_repositories": total_repos,
            "ranked_repositories": len(rows),
            "missing_repositories": missing,
            "period_growth": total_growth,
            "daily_average": round(total_growth / days, 1),
            "latest_update": latest,
        },
        "categories": categories,
        "languages": languages,
        "results": filtered,
    }


def build_repo_detail(con, full_name: str, period="7d", *, today=None):
    days = period_days(period)
    today = today or ghtrend.utc_day()
    repo = _repo_metadata(con, full_name)
    if not repo:
        raise KeyError(full_name)
    cutoff = today - timedelta(days=days)
    end = today - timedelta(days=1)
    raw = con.execute(
        "SELECT day,count FROM star_days WHERE repo_id=? AND day>=? AND day<? ORDER BY day",
        (repo["id"], cutoff.isoformat(), today.isoformat()),
    ).fetchall()
    by_day = {row["day"]: int(row["count"]) for row in raw}
    created = date.fromisoformat(repo["created_at"][:10])
    series = []
    values = []
    for offset in range(days):
        day = cutoff + timedelta(days=offset)
        key = day.isoformat()
        if key in by_day:
            value = by_day[key]
        elif day < created:
            value = 0
        else:
            value = None
        values.append(value)
        series.append({"date": key, "stars": value})

    complete = ghtrend.history_is_complete(repo, days, today)
    observed = [value for value in values if value is not None]
    gain = sum(observed) if complete else None
    average = round(gain / days, 1) if gain is not None else None
    best_day = max((point for point in series if point["stars"] is not None),
                   key=lambda point: point["stars"], default=None)
    moving_window = min(7, days)
    for index, point in enumerate(series):
        start = max(0, index - moving_window + 1)
        window = values[start:index + 1]
        point["moving_average"] = (round(sum(window) / len(window), 1)
                                   if window and all(v is not None for v in window)
                                   else None)
        point["cumulative"] = (sum(values[:index + 1])
                               if all(v is not None for v in values[:index + 1]) else None)

    topics = json.loads(repo["topics"] or "[]")
    category, category_basis = ghtrend.classify_repo(topics, repo["description"] or "")
    return {
        "repo": repo["full_name"],
        "url": repo["url"],
        "description": repo["description"] or "",
        "language": repo["language"] or "未标注",
        "topics": topics,
        "category": category,
        "category_basis": category_basis,
        "total_stars": int(repo["stars"]),
        "forks": int(repo["forks"]),
        "created_at": repo["created_at"],
        "history_updated_at": repo["history_fetched_at"],
        "period": period,
        "days": days,
        "window_start": cutoff.isoformat(),
        "window_end": end.isoformat(),
        "complete": complete,
        "growth": gain,
        "daily_average": average,
        "best_day": best_day,
        "series": series,
        "moving_average_window": moving_window,
    }


def _run_collector(limit=30, db_path=None):
    command = [sys.executable, str(BASE_DIR / "ghtrend.py")]
    if db_path:
        command.extend(["--db", str(db_path)])
    command.extend(["update", "--limit", str(limit)])
    try:
        process = subprocess.Popen(command, cwd=BASE_DIR, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   env=os.environ.copy())
        output, _ = process.communicate()
        returncode = process.returncode
    except OSError as exc:
        output, returncode = str(exc), 1
    with REFRESH_LOCK:
        REFRESH_STATE.update({
            "running": False,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "returncode": returncode,
            "output": (output or "")[-12000:],
        })


def start_refresh(limit=30, db_path=None):
    with REFRESH_LOCK:
        if REFRESH_STATE["running"]:
            return False
        REFRESH_STATE.update({
            "running": True,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "returncode": None,
            "output": "采集任务已启动……",
        })
    thread = threading.Thread(target=_run_collector, args=(limit, db_path), daemon=True)
    thread.start()
    return True


class DashboardHandler(BaseHTTPRequestHandler):
    db_path = ghtrend.default_db()

    def log_message(self, fmt, *args):
        print("[web] " + (fmt % args))

    def _send(self, status, payload, content_type="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if content_type.startswith("application/json") else str(payload).encode("utf-8")
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        length = min(int(self.headers.get("Content-Length", "0")), 2048)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/api/dashboard":
            period = params.get("period", ["7d"])[0]
            try:
                con = ghtrend.open_db(self.db_path)
                try:
                    payload = build_dashboard(
                        con, period,
                        search=params.get("q", [""])[0],
                        category=params.get("category", [""])[0],
                        language=params.get("language", [""])[0],
                        limit=int(params.get("limit", ["100"])[0]),
                    )
                finally:
                    con.close()
                self._send(200, payload)
            except (ValueError, TypeError) as exc:
                self._send(400, {"error": str(exc)})
            return
        if parsed.path == "/api/repo":
            name = params.get("name", [""])[0]
            period = params.get("period", ["7d"])[0]
            try:
                con = ghtrend.open_db(self.db_path)
                try:
                    payload = build_repo_detail(con, name, period)
                finally:
                    con.close()
                self._send(200, payload)
            except KeyError:
                self._send(404, {"error": "Repository not found"})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            return
        if parsed.path == "/api/refresh-status":
            with REFRESH_LOCK:
                self._send(200, dict(REFRESH_STATE))
            return
        if parsed.path == "/api/health":
            con = ghtrend.open_db(self.db_path)
            try:
                count = con.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
            finally:
                con.close()
            self._send(200, {"ok": True, "repositories": count})
            return
        if parsed.path == "/" or parsed.path == "/index.html":
            self._static("index.html")
            return
        if parsed.path in ("/app.js", "/styles.css"):
            self._static(parsed.path.lstrip("/"))
            return
        self._send(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/refresh":
            self._send(404, {"error": "Not found"})
            return
        try:
            body = self._json_body()
            limit = max(1, min(int(body.get("limit", 30)), 100))
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
            self._send(400, {"error": "Invalid request body"})
            return
        if not start_refresh(limit, self.db_path):
            self._send(409, {"error": "A collection job is already running"})
            return
        self._send(202, {"started": True, "message": "Collection started"})

    def _static(self, filename):
        path = WEB_DIR / filename
        if not path.is_file():
            self._send(404, {"error": "Static asset not found"})
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript",):
            content_type += "; charset=utf-8"
        self._send(200, path.read_bytes(), content_type)


def make_server(host="127.0.0.1", port=8765, db_path=None):
    handler = type("ConfiguredDashboardHandler", (DashboardHandler,), {
        "db_path": db_path or ghtrend.default_db(),
    })
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="GitHub Trends local web dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: localhost only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=ghtrend.default_db(), help="SQLite database path")
    args = parser.parse_args(argv)
    server = make_server(args.host, args.port, args.db)
    print(f"GitHub Trends Web is running: http://{args.host}:{args.port}")
    print(f"SQLite database: {args.db}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web server…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
