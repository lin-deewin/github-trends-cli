#!/usr/bin/env python3
"""Build a self-contained static GitHub Pages dashboard from the SQLite history."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import shutil

import ghtrend
import web

WEB_PERIODS = ("3d", "7d", "1m", "3m")
WEB_ASSETS = ("index.html", "app.js", "styles.css")


def build_payload(db_path: str, *, today: date | None = None, limit: int = 500) -> dict:
    """Build all dashboard and project-detail data needed by the static frontend."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")

    con = ghtrend.open_db(db_path)
    try:
        repo_count = int(con.execute("SELECT COUNT(*) FROM repos").fetchone()[0])
        if repo_count == 0:
            raise RuntimeError("SQLite database contains no repositories; run the collector first")

        today = today or ghtrend.utc_day()
        generated_at = datetime.now(timezone.utc).isoformat()
        dashboards = {}
        for period in WEB_PERIODS:
            dashboard = web.build_dashboard(con, period, limit=limit, today=today)
            dashboard["generated_at"] = generated_at
            dashboards[period] = dashboard

        names = [
            row["full_name"]
            for row in con.execute("SELECT full_name FROM repos ORDER BY full_name COLLATE NOCASE")
        ]
        details = {
            name: {
                period: web.build_repo_detail(con, name, period, today=today)
                for period in WEB_PERIODS
            }
            for name in names
        }

        latest_update = con.execute("SELECT MAX(observed_at) FROM repos").fetchone()[0]
        return {
            "schema_version": 1,
            "generated_at": generated_at,
            "data_through": (today - timedelta(days=1)).isoformat(),
            "repository_count": repo_count,
            "latest_update": latest_update,
            "periods": list(WEB_PERIODS),
            "dashboards": dashboards,
            "details": details,
        }
    finally:
        con.close()


def write_static_site(db_path: str, output_dir: str, *, limit: int = 500) -> dict:
    """Copy frontend assets and emit the versioned static JSON data bundle."""
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    source = Path(__file__).resolve().parent / "web"
    for filename in WEB_ASSETS:
        shutil.copy2(source / filename, output / filename)

    data_dir = output / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload(db_path, limit=limit)
    data_file = data_dir / "site-data.json"
    data_file.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (output / ".nojekyll").write_text("", encoding="utf-8")
    print(
        f"Static site generated: {output} "
        f"({payload['repository_count']} repositories, "
        f"{len(payload['periods'])} periods, {data_file.stat().st_size:,} bytes of data)"
    )
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the GitHub Trends static dashboard")
    parser.add_argument("--db", default=ghtrend.default_db(), help="SQLite database path")
    parser.add_argument("--output", default="dist", help="Static site output directory")
    parser.add_argument("--limit", type=int, default=500, help="Maximum ranked projects per period")
    args = parser.parse_args(argv)
    write_static_site(args.db, args.output, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
