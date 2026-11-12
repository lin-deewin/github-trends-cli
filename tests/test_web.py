"""Tests for the local web dashboard data layer."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import ghtrend
import web


class WebDashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ghtrend.open_db(str(Path(self.tmp.name) / "web.db"))
        self.today = date(2026, 11, 12)
        self.now = datetime(2026, 11, 12, 0, 30, tzinfo=timezone.utc)
        info = {
            "id": 501, "full_name": "sample/agent-kit", "description": "An AI agent toolkit",
            "language": "Python", "topics": ["ai-agent", "llm"],
            "html_url": "https://github.com/sample/agent-kit", "stargazers_count": 12500,
            "forks_count": 800, "created_at": "2024-01-01T00:00:00Z",
        }
        ghtrend.upsert_repo(self.db, info, now=self.now)
        sunday = self.today - timedelta(days=(self.today.weekday() + 1) % 7)
        weeks = []
        for i in range(30):
            week = sunday - timedelta(days=7 * i)
            weeks.append({
                "week": int(datetime.combine(week, datetime.min.time(), timezone.utc).timestamp()),
                "total": 28, "days": [1, 2, 3, 4, 5, 6, 7],
            })
        ghtrend.save_history(self.db, 501, weeks, now=self.now)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_period_mapping(self):
        self.assertEqual(web.period_days("3d"), 3)
        self.assertEqual(web.period_days("7d"), 7)
        self.assertEqual(web.period_days("1m"), 30)
        self.assertEqual(web.period_days("3m"), 90)
        with self.assertRaises(ValueError):
            web.period_days("6m")

    def test_dashboard_returns_metrics_and_repository(self):
        payload = web.build_dashboard(self.db, "7d", limit=20, today=self.today)
        self.assertEqual(payload["days"], 7)
        self.assertEqual(payload["summary"]["ranked_repositories"], 1)
        self.assertEqual(payload["results"][0]["repo"], "sample/agent-kit")
        self.assertEqual(payload["results"][0]["category"], "人工智能 / 大模型")
        self.assertEqual(payload["results"][0]["average_daily"], 4.0)
        self.assertEqual(len(payload["results"][0]["sparkline"]), 7)

    def test_dashboard_filters_search_category_and_language(self):
        self.assertEqual(len(web.build_dashboard(self.db, "3d", search="agent", today=self.today)["results"]), 1)
        self.assertEqual(len(web.build_dashboard(self.db, "3d", category="数据库与存储", today=self.today)["results"]), 0)
        self.assertEqual(len(web.build_dashboard(self.db, "3d", language="Rust", today=self.today)["results"]), 0)

    def test_detail_returns_daily_growth_and_moving_average(self):
        detail = web.build_repo_detail(self.db, "sample/agent-kit", "3d", today=self.today)
        self.assertTrue(detail["complete"])
        self.assertEqual(detail["growth"], 9)  # last three completed UTC days: 2 + 3 + 4
        self.assertEqual(detail["daily_average"], 3.0)
        self.assertEqual(len(detail["series"]), 3)
        self.assertEqual(detail["series"][0]["moving_average"], 2.0)
        self.assertEqual(detail["series"][-1]["moving_average"], 3.0)


    def test_scheduler_defaults_to_two_hours(self):
        self.assertEqual(web.DEFAULT_INTERVAL_HOURS, 2)
        self.assertEqual(web.SCHEDULER_STATE["interval_seconds"], 2 * 60 * 60)
        payload = web.scheduler_status_payload()
        self.assertEqual(payload["scheduler"]["interval_hours"], 2)
        self.assertIn("refresh", payload)

    def test_scheduler_rejects_invalid_interval(self):
        with self.assertRaises(ValueError):
            web.start_scheduler(0, db_path=str(Path(self.tmp.name) / "unused.db"))
        with self.assertRaises(ValueError):
            web.start_scheduler(169, db_path=str(Path(self.tmp.name) / "unused.db"))

    def test_scheduler_runs_immediately_then_waits_for_interval(self):
        class StopAfterOneWait:
            def __init__(self):
                self.stopped = False
                self.waited = None

            def is_set(self):
                return self.stopped

            def wait(self, seconds):
                self.waited = seconds
                self.stopped = True
                return True

        stop = StopAfterOneWait()
        previous_stop = web.SCHEDULER_STOP
        try:
            web.SCHEDULER_STOP = stop
            with patch.object(web, "start_refresh", return_value=True) as start:
                web._scheduler_loop(7200, 30, str(Path(self.tmp.name) / "web.db"))
            start.assert_called_once_with(30, str(Path(self.tmp.name) / "web.db"), trigger="scheduled")
            self.assertEqual(stop.waited, 7200)
            self.assertFalse(web.SCHEDULER_STATE["enabled"])
        finally:
            web.SCHEDULER_STOP = previous_stop

    def test_scheduler_skips_tick_when_another_collection_is_running(self):
        class StopAfterOneWait:
            def __init__(self):
                self.stopped = False

            def is_set(self):
                return self.stopped

            def wait(self, seconds):
                self.stopped = True
                return True

        stop = StopAfterOneWait()
        previous_stop = web.SCHEDULER_STOP
        try:
            web.SCHEDULER_STOP = stop
            with patch.object(web, "start_refresh", return_value=False) as start:
                web._scheduler_loop(7200, 30, str(Path(self.tmp.name) / "web.db"))
            start.assert_called_once_with(30, str(Path(self.tmp.name) / "web.db"), trigger="scheduled")
            self.assertEqual(web.SCHEDULER_STATE["last_outcome"], "skipped_busy")
        finally:
            web.SCHEDULER_STOP = previous_stop

    def test_detail_rejects_unknown_repository(self):
        with self.assertRaises(KeyError):
            web.build_repo_detail(self.db, "missing/repo", "7d", today=self.today)


if __name__ == "__main__":
    unittest.main()
