"""Tests for static GitHub Pages data generation."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import tempfile
import unittest

import build_site
import ghtrend


class StaticSiteBuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = str(self.root / "stats.sqlite3")
        db = ghtrend.open_db(self.db_path)
        today = date(2026, 11, 12)
        now = datetime(2026, 11, 12, 0, 30, tzinfo=timezone.utc)
        ghtrend.upsert_repo(db, {
            "id": 9001,
            "full_name": "sample/static-agent",
            "description": "A static site test AI agent",
            "language": "Python",
            "topics": ["ai-agent", "llm"],
            "html_url": "https://github.com/sample/static-agent",
            "stargazers_count": 1200,
            "forks_count": 90,
            "created_at": "2024-01-01T00:00:00Z",
        }, now=now)
        sunday = today - timedelta(days=(today.weekday() + 1) % 7)
        weeks = []
        for offset in range(30):
            week = sunday - timedelta(days=7 * offset)
            weeks.append({
                "week": int(datetime.combine(week, datetime.min.time(), timezone.utc).timestamp()),
                "total": 28,
                "days": [1, 2, 3, 4, 5, 6, 7],
            })
        ghtrend.save_history(db, 9001, weeks, now=now)
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_payload_contains_all_web_periods_and_project_details(self):
        payload = build_site.build_payload(self.db_path, today=date(2026, 11, 12))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["periods"], ["3d", "7d", "1m", "3m"])
        self.assertEqual(payload["repository_count"], 1)
        self.assertEqual(set(payload["dashboards"]), {"3d", "7d", "1m", "3m"})
        detail = payload["details"]["sample/static-agent"]
        self.assertTrue(detail["3d"]["complete"])
        self.assertEqual(detail["3d"]["growth"], 9)
        self.assertEqual(len(detail["3m"]["series"]), 90)
        self.assertEqual(payload["dashboards"]["7d"]["results"][0]["repo"], "sample/static-agent")

    def test_static_site_writes_assets_and_json(self):
        output = self.root / "dist"
        build_site.write_static_site(self.db_path, str(output), limit=100)
        self.assertTrue((output / "index.html").is_file())
        self.assertTrue((output / "app.js").is_file())
        self.assertTrue((output / "styles.css").is_file())
        self.assertTrue((output / ".nojekyll").is_file())
        payload = json.loads((output / "data" / "site-data.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)

    def test_empty_database_is_rejected(self):
        empty_path = str(self.root / "empty.sqlite3")
        db = ghtrend.open_db(empty_path)
        db.close()
        with self.assertRaisesRegex(RuntimeError, "no repositories"):
            build_site.build_payload(empty_path, today=date(2026, 11, 12))


if __name__ == "__main__":
    unittest.main()
