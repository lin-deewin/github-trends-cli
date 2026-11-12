"""Run: python -m unittest discover -s tests -v"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from argparse import Namespace
from contextlib import redirect_stdout, redirect_stderr
import io

import ghtrend


class TrendTest(unittest.TestCase):
    def test_parse_trending_only_project_heading(self):
        html = '''<a href="/ignore/home">nav</a>
            <article class="Box-row"><h2 class="h3 lh-condensed">
              <a href="/owner/repo">owner / repo</a></h2><a href="/owner/repo/stargazers">stars</a></article>
            <article class="Box-row"><h2><a href="/Acme/Tool">x</a></h2></article>'''
        self.assertEqual(ghtrend.parse_trending(html), ["owner/repo", "Acme/Tool"])

    def test_parse_empty_html(self):
        self.assertEqual(ghtrend.parse_trending("<html>empty</html>"), [])

    def test_validate_repo(self):
        self.assertEqual(ghtrend.valid_repo_name("/owner/repo/"), "owner/repo")
        with self.assertRaises(ValueError):
            ghtrend.valid_repo_name("https://github.com/owner/repo")


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ghtrend.open_db(str(Path(self.tmp.name) / "stats.db"))
        self.today = date(2026, 11, 12)
        self.now = datetime(2026, 11, 12, 0, 30, tzinfo=timezone.utc)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def make_repo(self, rid=1, name="example/repo", stars=200, topics=None,
                  description="Test"):
        info = {"id":rid, "full_name":name,"stargazers_count":stars,
                "forks_count":10, "created_at":"2020-01-01T00:00:00Z",
                "language":"Python","description":description,
                "topics": topics or [], "html_url":"https://github.com/"+name}
        ghtrend.upsert_repo(self.db, info, now=self.now)

    def make_weeks(self, count=30):
        week = self.today - timedelta(days=(self.today.weekday()+1)%7)
        return [{"week":int(datetime.combine(week-timedelta(days=7*i), datetime.min.time(), timezone.utc).timestamp()),
                 "total":7,"days":[1]*7} for i in range(count)]

    def test_six_windows_from_initial_history(self):
        self.make_repo()
        self.assertTrue(ghtrend.save_history(self.db, 1, self.make_weeks(), now=self.now))
        for days in ghtrend.PERIODS:
            rows, missing = ghtrend.ranked_rows(self.db, days, today=self.today)
            self.assertEqual((rows[0]["gain"], missing), (days, 0))

    def test_no_fabricated_six_month_data(self):
        self.make_repo()
        ghtrend.save_history(self.db, 1, self.make_weeks(3), now=self.now)
        rows, missing = ghtrend.ranked_rows(self.db, 180, today=self.today)
        self.assertEqual(rows, [])
        self.assertEqual(missing, 1)

    def test_history_excludes_partial_today(self):
        self.make_repo()
        weeks = self.make_weeks()
        weeks[0]["days"][(self.today.weekday()+1)%7] = 1000
        ghtrend.save_history(self.db, 1, weeks, now=self.now)
        rows, _ = ghtrend.ranked_rows(self.db, 1, today=self.today)
        self.assertEqual(rows[0]["gain"], 1)

    def test_history_zero_pre_creation(self):
        self.make_repo()
        self.db.execute("UPDATE repos SET created_at='2026-11-10T00:00:00Z' WHERE id=1")
        self.db.commit()
        ghtrend.save_history(self.db, 1, self.make_weeks(1), now=self.now)
        rows, missing = ghtrend.ranked_rows(self.db, 180, today=self.today)
        self.assertEqual(missing, 0)
        self.assertEqual(len(rows), 1)

    def test_topics_are_saved_and_classified(self):
        self.make_repo(topics=["llm", "ai-agent", "python"])
        ghtrend.save_history(self.db, 1, self.make_weeks(), now=self.now)
        rows, _ = ghtrend.ranked_rows(self.db, 1, today=self.today)
        self.assertEqual(rows[0]["category"], "人工智能 / 大模型")
        self.assertEqual(rows[0]["topics"], ["ai-agent", "llm", "python"])
        self.assertEqual(rows[0]["category_basis"], "GitHub Topics")

    def test_description_keyword_fallback(self):
        category, basis = ghtrend.classify_repo([], "An open-source framework for building AI agents")
        self.assertEqual(category, "人工智能 / 大模型")
        self.assertIn("自动归类", basis)

    def test_report_includes_url_description_and_category(self):
        self.make_repo(topics=["llm", "ai-agent"],
                       description="An open-source toolkit for building AI agents")
        ghtrend.save_history(self.db, 1, self.make_weeks(), now=self.now)
        args = Namespace(db=str(Path(self.tmp.name) / "stats.db"), source="history",
                         language="", limit=1)
        output = io.StringIO()
        with redirect_stdout(output):
            ghtrend.report_command(args)
        report = output.getvalue()
        self.assertIn("URL：https://github.com/example/repo", report)
        self.assertIn("分类：人工智能 / 大模型", report)
        self.assertIn("简介：An open-source toolkit for building AI agents", report)
        self.assertIn("Topics：ai-agent, llm", report)

    def test_snapshot_net_change_and_missing(self):
        self.make_repo(stars=200)
        earlier = self.now - timedelta(days=3)
        self.make_repo(stars=200)
        self.db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?)", (1, earlier.date().isoformat(), earlier.isoformat(), 180, 8))
        self.db.commit()
        rows, missing = ghtrend.ranked_rows(self.db, 3, source="snapshot", today=self.today)
        self.assertEqual(rows[0]["gain"], 20)
        self.assertEqual(missing, 0)
        rows, missing = ghtrend.ranked_rows(self.db, 180, source="snapshot", today=self.today)
        self.assertEqual((rows, missing), ([], 1))


class MigrationTest(unittest.TestCase):
    def test_add_topics_column_to_previous_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            import sqlite3
            old = sqlite3.connect(db_path)
            old.execute("""CREATE TABLE repos (
                id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, description TEXT,
                language TEXT, url TEXT NOT NULL, stars INTEGER NOT NULL DEFAULT 0,
                forks INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, observed_at TEXT NOT NULL
            )""")
            old.commit()
            old.close()
            con = ghtrend.open_db(str(db_path))
            columns = {row["name"] for row in con.execute("PRAGMA table_info(repos)")}
            self.assertIn("topics", columns)
            con.close()


class OfflineIntegrationTest(unittest.TestCase):
    def test_update_report_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "integration.db")
            today = ghtrend.utc_day()
            sunday = today - timedelta(days=(today.weekday()+1) % 7)
            weeks = [{"week": int(datetime.combine(sunday - timedelta(days=7 * i),
                         datetime.min.time(), timezone.utc).timestamp()),
                      "total": 14, "days": [2]*7} for i in range(30)]
            repo = {"id": 400, "full_name": "sample/demo", "description": "A developer tool for AI agents",
                    "topics": ["ai-agent", "developer-tools"],
                    "language": "Python", "html_url": "https://github.com/sample/demo",
                    "stargazers_count": 1234, "forks_count": 44,
                    "created_at": "2020-01-01T00:00:00Z"}

            class FakeClient:
                def __init__(self, *args, **kwargs):
                    self.requests = 0
                def get(self, url, *, json_response=True):
                    self.requests += 1
                    return '<article class="Box-row"><h2><a href="/sample/demo">x</a></h2></article>'
                def api(self, path, **query):
                    self.requests += 1
                    if path == "/search/repositories":
                        return {"items": [repo]}
                    if path == "/repos/sample/demo/stargazers/history":
                        return weeks
                    if path == "/repos/sample/demo":
                        return repo
                    raise AssertionError(path)

            args = Namespace(db=db_path, limit=5, timeout=1, language="",
                             skip_trending=False, skip_search=False)
            with patch.object(ghtrend, "HTTPClient", FakeClient), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = ghtrend.update(args)
            self.assertEqual(result, 0)
            con = ghtrend.open_db(db_path)
            for days in ghtrend.PERIODS:
                rows, missing = ghtrend.ranked_rows(con, days, today=today)
                self.assertEqual((len(rows), rows[0]["gain"], missing), (1, days*2, 0))
            con.close()


if __name__ == "__main__":
    unittest.main()
