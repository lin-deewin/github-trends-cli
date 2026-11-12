#!/usr/bin/env python3
"""GitHub popularity tracker: six daily Star-growth leaderboards, without dependencies.

Python 3.10+. Public GitHub repositories only. Dates and complete-day windows are UTC.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import textwrap
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.github.com"
GITHUB_BASE = "https://github.com"
API_VERSION = "2026-03-10"
PERIODS = (1, 3, 7, 30, 90, 180)
FULL_NAME = re.compile(r"^[\w.-]+/[\w.-]+$", re.ASCII)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_day() -> date:
    return utc_now().date()


class GitHubError(RuntimeError):
    pass


class RateLimitError(GitHubError):
    pass


class HTTPClient:
    def __init__(self, token: str | None = None, timeout: float = 20):
        self.token = token or None
        self.timeout = timeout
        self.requests = 0

    def get(self, url: str, *, json_response: bool = True):
        headers = {
            "Accept": "application/vnd.github+json" if json_response else "text/html",
            "User-Agent": "github-trends-cli/1.0",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if self.token and url.startswith(API_BASE + "/"):
            headers["Authorization"] = "Bearer " + self.token
        request = Request(url, headers=headers)
        for attempt in range(3):
            self.requests += 1
            try:
                with urlopen(request, timeout=self.timeout) as resp:
                    body = resp.read()
                    return json.loads(body.decode("utf-8")) if json_response else body.decode("utf-8", "replace")
            except HTTPError as e:
                message = e.reason or "HTTP error"
                try:
                    payload = json.loads(e.read().decode("utf-8", "replace"))
                    message = payload.get("message", message)
                except (ValueError, UnicodeError):
                    pass
                if e.code in (403, 429) and ("rate limit" in str(message).lower() or
                        e.headers.get("X-RateLimit-Remaining") == "0" or e.code == 429):
                    raise RateLimitError(f"GitHub API rate limit reached: {message}") from e
                if e.code in (500, 502, 503, 504) and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise GitHubError(f"HTTP {e.code}: {message} ({url})") from e
            except (URLError, TimeoutError, OSError) as e:
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise GitHubError(f"Network error: {e} ({url})") from e
        raise GitHubError("Unexpected request failure")

    def api(self, path: str, **query):
        url = API_BASE + path
        if query:
            url += "?" + urlencode(query)
        return self.get(url)


class TrendingParser(HTMLParser):
    """Extract owner/repo ONLY from article.Box-row > h2 anchors, not page navigation."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.article_depth = 0
        self.heading_depth = 0
        self.repos = []
        self.seen = set()

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        if tag == "article" and "Box-row" in props.get("class", "").split():
            self.article_depth += 1
        elif tag == "article" and self.article_depth:
            self.article_depth += 1
        if self.article_depth and tag == "h2":
            self.heading_depth += 1
        if self.article_depth and self.heading_depth and tag == "a":
            name = props.get("href", "").split("?", 1)[0].strip("/")
            if FULL_NAME.fullmatch(name) and name.lower() not in self.seen:
                self.seen.add(name.lower())
                self.repos.append(name)

    def handle_endtag(self, tag):
        if tag == "h2" and self.heading_depth:
            self.heading_depth -= 1
        if tag == "article" and self.article_depth:
            self.article_depth -= 1
            if not self.article_depth:
                self.heading_depth = 0


def parse_trending(html: str) -> list[str]:
    parser = TrendingParser()
    parser.feed(html)
    return parser.repos


def get_trending(client: HTTPClient, period: str, language: str = "") -> list[str]:
    path = "/trending"
    if language:
        path += "/" + quote(language.strip(), safe="")
    url = GITHUB_BASE + path + "?" + urlencode({"since": period})
    return parse_trending(client.get(url, json_response=False))


def valid_repo_name(full_name: str) -> str:
    name = full_name.strip().strip("/")
    if not FULL_NAME.fullmatch(name):
        raise ValueError("Repository must be in owner/repo format")
    return name


def default_db() -> str:
    return os.environ.get("GHTREND_DB", str(Path(__file__).resolve().parent / "ghtrend.sqlite3"))


def open_db(path: str):
    db_path = Path(path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS repos (
            id INTEGER PRIMARY KEY,
            full_name TEXT NOT NULL,
            description TEXT,
            language TEXT,
            topics TEXT NOT NULL DEFAULT '[]',
            url TEXT NOT NULL,
            stars INTEGER NOT NULL DEFAULT 0,
            forks INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            observed_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_repos_name ON repos(full_name COLLATE NOCASE);
        CREATE TABLE IF NOT EXISTS snapshots (
            repo_id INTEGER NOT NULL REFERENCES repos(id),
            observed_day TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            stars INTEGER NOT NULL,
            forks INTEGER NOT NULL,
            PRIMARY KEY(repo_id, observed_day)
        );
        CREATE TABLE IF NOT EXISTS star_days (
            repo_id INTEGER NOT NULL REFERENCES repos(id),
            day TEXT NOT NULL,
            count INTEGER NOT NULL CHECK(count >= 0),
            PRIMARY KEY(repo_id, day)
        );
        CREATE TABLE IF NOT EXISTS history_status (
            repo_id INTEGER PRIMARY KEY REFERENCES repos(id),
            fetched_at TEXT NOT NULL,
            start_day TEXT NOT NULL,
            end_day TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS watchlist (
            full_name TEXT NOT NULL COLLATE NOCASE PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS trending_seen (
            full_name TEXT NOT NULL COLLATE NOCASE,
            observed_day TEXT NOT NULL,
            period TEXT NOT NULL,
            language TEXT NOT NULL,
            rank INTEGER NOT NULL,
            PRIMARY KEY(full_name, observed_day, period, language)
        );
    """)
    # Backward-compatible migration for databases created by earlier releases.
    repo_columns = {row["name"] for row in con.execute("PRAGMA table_info(repos)")}
    if "topics" not in repo_columns:
        con.execute("ALTER TABLE repos ADD COLUMN topics TEXT NOT NULL DEFAULT '[]'")
    con.commit()
    return con


def upsert_repo(con, info: dict, *, now: datetime | None = None):
    now = now or utc_now()
    name = valid_repo_name(info["full_name"])
    rid = int(info["id"])
    if int(info.get("stargazers_count", 0)) < 0:
        raise ValueError("Invalid Star count")
    raw_topics = info.get("topics") or []
    if isinstance(raw_topics, str):
        raw_topics = [part.strip() for part in raw_topics.split(",") if part.strip()]
    topics = sorted({str(topic).strip().lower() for topic in raw_topics if str(topic).strip()})
    values = (
        rid, name, info.get("description"), info.get("language"),
        json.dumps(topics, ensure_ascii=False),
        info.get("html_url") or GITHUB_BASE + "/" + name,
        int(info.get("stargazers_count", 0)), int(info.get("forks_count", 0)),
        info["created_at"], now.isoformat(),
    )
    with con:
        # If a repo moved, the GitHub numeric ID stays stable; update the name.
        con.execute("""
            INSERT INTO repos(id, full_name, description, language, topics, url, stars, forks, created_at, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              full_name=excluded.full_name, description=excluded.description,
              language=excluded.language, topics=excluded.topics, url=excluded.url, stars=excluded.stars,
              forks=excluded.forks, created_at=excluded.created_at,
              observed_at=excluded.observed_at
        """, values)
        con.execute("""
            INSERT INTO snapshots(repo_id, observed_day, captured_at, stars, forks)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repo_id, observed_day) DO UPDATE SET
              captured_at=excluded.captured_at, stars=excluded.stars, forks=excluded.forks
        """, (rid, now.date().isoformat(), now.isoformat(), values[6], values[7]))
    return rid


def parse_history_weeks(weeks: list[dict], *, today: date | None = None):
    """Translate GitHub calendar-week records to date buckets. Never guess absent weeks."""
    today = today or utc_day()
    if not isinstance(weeks, list):
        raise ValueError("Expected history JSON list")
    daily = {}
    for item in weeks:
        if not isinstance(item, dict) or not isinstance(item.get("days"), list) or len(item["days"]) != 7:
            raise ValueError("Unexpected GitHub star history response")
        week = datetime.fromtimestamp(int(item["week"]), timezone.utc).date()
        for offset, raw in enumerate(item["days"]):
            value = int(raw)
            if value < 0:
                raise ValueError("Negative historical Star count")
            day = week + timedelta(days=offset)
            if day <= today:
                daily[day.isoformat()] = value
    return daily


def save_history(con, rid: int, weeks: list[dict], *, now: datetime | None = None):
    now = now or utc_now()
    data = parse_history_weeks(weeks, today=now.date())
    if not data:
        return False
    start_day, end_day = min(data), max(data)
    with con:
        con.execute("DELETE FROM star_days WHERE repo_id=?", (rid,))
        con.executemany("INSERT INTO star_days(repo_id,day,count) VALUES (?,?,?)",
                        ((rid, day, count) for day, count in data.items()))
        con.execute("""
            INSERT INTO history_status(repo_id,fetched_at,start_day,end_day)
            VALUES (?,?,?,?) ON CONFLICT(repo_id) DO UPDATE SET
            fetched_at=excluded.fetched_at,start_day=excluded.start_day,end_day=excluded.end_day
        """, (rid, now.isoformat(), start_day, end_day))
    return True


def history_is_complete(repo: sqlite3.Row, days: int, today: date) -> bool:
    # Last N complete UTC days: [today-N, today), not today's incomplete bucket.
    start = today - timedelta(days=days)
    end = today - timedelta(days=1)
    if not repo["history_start"] or not repo["history_end"]:
        return False
    created = date.fromisoformat(repo["created_at"][:10])
    return (date.fromisoformat(repo["history_start"]) <= start or created >= start) and \
           date.fromisoformat(repo["history_end"]) >= end


TOPIC_CATEGORIES = (
    ("人工智能 / 大模型", {
        "ai", "artificial-intelligence", "machine-learning", "deep-learning",
        "llm", "large-language-model", "generative-ai", "ai-agent", "ai-agents",
        "agentic-ai", "agents", "rag", "natural-language-processing", "nlp",
        "computer-vision", "stable-diffusion", "diffusion-models", "transformers",
        "pytorch", "tensorflow", "huggingface",
    }),
    ("开发者工具", {
        "developer-tools", "devtools", "cli", "command-line", "terminal", "ide",
        "editor", "code-editor", "vscode", "neovim", "vim", "emacs", "git",
        "git-tools", "debugger", "linter", "formatter", "testing", "test-framework",
        "build-tool", "package-manager",
    }),
    ("Web / 前端", {
        "web", "frontend", "front-end", "react", "vue", "angular", "svelte",
        "nextjs", "nuxt", "javascript", "typescript", "css", "html", "tailwindcss",
    }),
    ("后端 / API", {
        "backend", "back-end", "api", "rest-api", "graphql", "grpc", "microservices",
        "spring-boot", "nodejs", "express", "fastapi", "django", "flask", "rails",
    }),
    ("数据库与存储", {
        "database", "databases", "sql", "nosql", "postgres", "postgresql", "mysql",
        "mongodb", "redis", "sqlite", "vector-database", "elasticsearch", "search-engine",
    }),
    ("云原生 / DevOps", {
        "devops", "kubernetes", "k8s", "docker", "container", "cloud-native",
        "terraform", "ansible", "ci-cd", "github-actions", "monitoring", "observability",
    }),
    ("安全与隐私", {
        "security", "cybersecurity", "pentesting", "penetration-testing", "vulnerability",
        "encryption", "privacy", "authentication",
    }),
    ("数据科学 / 分析", {
        "data-science", "data-engineering", "analytics", "big-data", "etl",
        "data-visualization", "jupyter",
    }),
    ("系统 / 底层技术", {
        "linux", "operating-system", "kernel", "systems-programming", "compiler",
        "programming-language", "runtime", "embedded", "firmware", "networking",
    }),
    ("移动开发", {
        "android", "ios", "mobile", "flutter", "react-native", "swift",
        "kotlin-multiplatform",
    }),
    ("游戏 / 图形", {
        "game", "gamedev", "game-engine", "graphics", "rendering", "3d",
        "opengl", "vulkan", "blender",
    }),
    ("区块链 / Web3", {
        "blockchain", "web3", "ethereum", "solidity", "smart-contract", "cryptocurrency",
    }),
    ("文档 / 知识管理", {
        "documentation", "docs", "wiki", "notes", "knowledge-management",
        "static-site-generator", "blog", "markdown",
    }),
)

DESCRIPTION_CATEGORY_HINTS = (
    ("人工智能 / 大模型", re.compile(r"\b(ai|artificial intelligence|machine learning|deep learning|llm|large language model|agentic|ai agent|rag|generative ai)\b", re.I)),
    ("数据库与存储", re.compile(r"\b(database|databases|sql|nosql|vector database|key.value store)\b", re.I)),
    ("云原生 / DevOps", re.compile(r"\b(devops|kubernetes|docker|container orchestration|cloud.native|deployment|monitoring)\b", re.I)),
    ("安全与隐私", re.compile(r"\b(security|cybersecurity|encryption|privacy|penetration testing)\b", re.I)),
    ("Web / 前端", re.compile(r"\b(frontend|front.end|react|vue.js|web framework|css framework)\b", re.I)),
    ("后端 / API", re.compile(r"\b(backend|back.end|api server|rest api|graphql|microservice)\b", re.I)),
    ("开发者工具", re.compile(r"\b(developer tool|development tool|command.line|cli tool|code editor|debugger|formatter|linter)\b", re.I)),
    ("数据科学 / 分析", re.compile(r"\b(data science|data analysis|data engineering|analytics|data visualization)\b", re.I)),
    ("移动开发", re.compile(r"\b(android|ios|mobile app|mobile development|flutter)\b", re.I)),
    ("游戏 / 图形", re.compile(r"\b(game engine|video game|game development|3d rendering|graphics engine)\b", re.I)),
    ("区块链 / Web3", re.compile(r"\b(blockchain|web3|ethereum|smart contract|cryptocurrency)\b", re.I)),
    ("系统 / 底层技术", re.compile(r"\b(operating system|linux kernel|compiler|programming language|systems programming|embedded)\b", re.I)),
)


def classify_repo(topics, description: str = "") -> tuple[str, str]:
    """Return a cautious, human-readable category and its evidence source."""
    normalized = {str(topic).strip().lower().replace("_", "-") for topic in (topics or [])}
    matched = [label for label, aliases in TOPIC_CATEGORIES if normalized.intersection(aliases)]
    if matched:
        return " / ".join(matched[:2]), "GitHub Topics"
    for label, pattern in DESCRIPTION_CATEGORY_HINTS:
        if pattern.search(description or ""):
            return label, "项目简介关键词（自动归类）"
    return "未分类", "GitHub 未提供可识别的主题标签"


def ranked_rows(con, days: int, *, source="history", language=None, today=None):
    if days not in PERIODS:
        raise ValueError(f"days must be one of {PERIODS}")
    if source not in ("history", "snapshot"):
        raise ValueError("source must be history or snapshot")
    today = today or utc_day()
    cutoff = today - timedelta(days=days)
    end = today - timedelta(days=1)
    repos = con.execute("""
        SELECT r.*, h.start_day AS history_start, h.end_day AS history_end,
               h.fetched_at AS history_fetched_at
        FROM repos r LEFT JOIN history_status h ON h.repo_id=r.id
    """).fetchall()
    rows = []
    missing = 0
    for repo in repos:
        if language and (repo["language"] or "").casefold() != language.casefold():
            continue
        value = None
        if source == "history":
            if history_is_complete(repo, days, today):
                value = con.execute("""
                    SELECT COALESCE(SUM(count),0) FROM star_days
                    WHERE repo_id=? AND day>=? AND day<?
                """, (repo["id"], cutoff.isoformat(), today.isoformat())).fetchone()[0]
        else:
            snapshots = con.execute("""
                SELECT observed_day, captured_at, stars FROM snapshots
                WHERE repo_id=? ORDER BY captured_at DESC
            """, (repo["id"],)).fetchall()
            if snapshots:
                latest = snapshots[0]
                # A stale latest snapshot must not be presented as today's ranking.
                if date.fromisoformat(latest["observed_day"]) < today - timedelta(days=1):
                    missing += 1
                    continue
                try:
                    base = datetime.fromisoformat(latest["captured_at"])
                    target = base - timedelta(days=days)
                    candidates = [s for s in snapshots[1:]
                                  if abs((datetime.fromisoformat(s["captured_at"]) - target).total_seconds()) <= 43200]
                    if candidates:
                        nearest = min(candidates, key=lambda s: abs((datetime.fromisoformat(s["captured_at"]) - target).total_seconds()))
                        value = latest["stars"] - nearest["stars"]
                except (ValueError, TypeError):
                    pass
        if value is None:
            missing += 1
            continue
        try:
            topics = json.loads(repo["topics"] or "[]")
        except (TypeError, ValueError):
            topics = []
        category, category_basis = classify_repo(topics, repo["description"] or "")
        rows.append({
            "repo": repo["full_name"], "language": repo["language"] or "-",
            "gain": int(value), "total_stars": repo["stars"],
            "url": repo["url"], "description": repo["description"] or "",
            "topics": topics, "category": category, "category_basis": category_basis,
            "days": days, "source": source,
            "window_start_utc": cutoff.isoformat(), "window_end_utc": end.isoformat(),
        })
    rows.sort(key=lambda x: (-x["gain"], -x["total_stars"], x["repo"].lower()))
    return rows, missing


def search_queries(today: date) -> list[str]:
    # These are DISCOVERY queries, not growth metrics. No claim of global coverage.
    return [
        f"created:>={today - timedelta(days=7)} stars:>=10 fork:false",
        f"created:>={today - timedelta(days=30)} stars:>=50 fork:false",
        f"created:>={today - timedelta(days=180)} stars:>=200 fork:false",
        f"pushed:>={today - timedelta(days=7)} stars:>=3000 fork:false",
    ]


def discover(client: HTTPClient, con, *, use_trending=True, use_search=True,
             language="", today=None):
    today = today or utc_day()
    candidates = {}  # preserve order (first seen first)
    searches = {}
    watched = [r[0] for r in con.execute("SELECT full_name FROM watchlist ORDER BY full_name")]
    for name in watched:
        candidates[name.casefold()] = name
    if use_trending:
        for period in ("daily", "weekly", "monthly"):
            try:
                names = get_trending(client, period, language)
                if not names:
                    print(f"Warning: Trending {period} returned no projects; layout may have changed", file=sys.stderr)
                for rank, name in enumerate(names, 1):
                    candidates.setdefault(name.casefold(), name)
                    with con:
                        con.execute("""
                            INSERT INTO trending_seen(full_name,observed_day,period,language,rank)
                            VALUES (?,?,?,?,?) ON CONFLICT DO UPDATE SET rank=excluded.rank
                        """, (name, today.isoformat(), period, language, rank))
            except GitHubError as e:
                print(f"Warning: Trending {period}: {e}", file=sys.stderr)
    if use_search:
        for query in search_queries(today):
            if language:
                query += f" language:{language}"
            try:
                result = client.api("/search/repositories", q=query, sort="stars", order="desc", per_page=50)
                for item in result.get("items", []):
                    name = item.get("full_name")
                    if name and FULL_NAME.fullmatch(name):
                        candidates.setdefault(name.casefold(), name)
                        searches[name.casefold()] = item
            except RateLimitError:
                print("Warning: GitHub Search rate limited, continuing with discovered candidates", file=sys.stderr)
                break
            except GitHubError as e:
                print(f"Warning: GitHub Search: {e}", file=sys.stderr)
    return candidates, searches


def update(args):
    con = open_db(args.db)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    client = HTTPClient(token, timeout=args.timeout)
    limit = args.limit if args.limit is not None else (40 if token else 20)
    if not token:
        print("Info: no GITHUB_TOKEN; unauthenticated GitHub API rate limit is low. "
              "Use --limit 10 if rate limited.", file=sys.stderr)
    candidates, searches = discover(client, con, use_trending=not args.skip_trending,
                                    use_search=not args.skip_search, language=args.language)
    chosen = list(candidates.values())[:limit]
    print(f"Found {len(candidates)} candidate repositories; collecting up to {len(chosen)}.", flush=True)
    if not chosen:
        print("No repositories found. Check network access or run 'watch add owner/repo'.", file=sys.stderr)
        return 2
    successful = history_count = 0
    for index, name in enumerate(chosen, 1):
        try:
            # Search provides complete metadata; Trending-only repos require a repo GET.
            info = searches.get(name.casefold())
            if info is None:
                owner, repo = name.split("/", 1)
                info = client.api(f"/repos/{quote(owner,safe='')}/{quote(repo,safe='')}")
            rid = upsert_repo(con, info)
            successful += 1
            try:
                owner, repo = info["full_name"].split("/", 1)
                history = client.api(f"/repos/{quote(owner,safe='')}/{quote(repo,safe='')}/stargazers/history",
                                     per_page=30, page=1)
                if save_history(con, rid, history):
                    history_count += 1
                else:
                    print(f"[{index}/{len(chosen)}] {name}: history empty/unavailable", file=sys.stderr)
            except RateLimitError:
                # Stop immediately: retries against an exhausted API limit make things worse.
                raise
            except (GitHubError, ValueError, TypeError) as e:
                print(f"[{index}/{len(chosen)}] {name}: history unavailable ({e}); snapshots retained", file=sys.stderr)
        except RateLimitError as e:
            print(f"Stopped due to rate limit: {e}", file=sys.stderr)
            break
        except (GitHubError, ValueError, TypeError, KeyError, sqlite3.IntegrityError) as e:
            print(f"[{index}/{len(chosen)}] Skipping {name}: {e}", file=sys.stderr)
        if index % 5 == 0 or index == len(chosen):
            print(f"  Processed {index}/{len(chosen)}; saved {successful} repositories, "
                  f"{history_count} star histories", flush=True)
    print(f"Done: {successful} repositories updated, {history_count} historical series, "
          f"{client.requests} HTTP requests. Database: {args.db}")
    if successful:
        print("Run: python ghtrend.py report")
        return 0
    return 2


def display_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - display_width(s))


def table_output(rows: list[dict], *, limit: int):
    show = rows[:limit]
    columns = [("#", "rank", 3), ("项目", "repo", 38), ("语言", "language", 14),
               ("新增★", "gain", 12), ("总★", "total_stars", 12)]
    header = "  ".join(pad(title, width) for title, _, width in columns)
    print(header)
    print("-" * display_width(header))
    for i, row in enumerate(show, 1):
        values = {**row, "rank": str(i), "gain": f"{row['gain']:+,}",
                  "total_stars": f"{row['total_stars']:,}"}
        parts = []
        for _, key, width in columns:
            value = str(values[key])
            if display_width(value) > width:
                # Keep terminals reasonably readable, do not break CJK characters.
                trimmed = ""
                for c in value:
                    if display_width(trimmed + c + "…") > width:
                        break
                    trimmed += c
                value = trimmed + "…"
            parts.append(pad(value, width))
        print("  ".join(parts))


def detailed_output(rows: list[dict], *, limit: int):
    """Print readable project cards for reports, including links and purpose."""
    for rank, row in enumerate(rows[:limit], 1):
        print(f"{rank:>2}. {row['repo']}  |  {row['gain']:+,} ★  |  总 Star {row['total_stars']:,}  |  {row['language']}")
        print(f"    URL：{row['url']}")
        print(f"    分类：{row['category']}（依据：{row['category_basis']}）")
        if row.get("topics"):
            print(f"    Topics：{', '.join(row['topics'])}")
        description = " ".join((row.get("description") or "").split())
        if description:
            wrapped = textwrap.fill(description, width=100, initial_indent="    简介：",
                                    subsequent_indent="          ")
            print(wrapped)
        else:
            print("    简介：GitHub 未填写项目描述，请打开项目主页查看 README。")
        print()


def top_command(args):
    con = open_db(args.db)
    rows, missing = ranked_rows(con, args.days, source=args.source, language=args.language)
    selection = rows[:args.limit]
    if args.format == "json":
        output = json.dumps({"period_days": args.days, "source": args.source,
                             "available": len(rows), "missing": missing,
                             "results": selection}, ensure_ascii=False, indent=2)
    elif args.format == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=("repo", "language", "category", "topics", "description", "url", "gain", "total_stars", "days", "source", "window_start_utc", "window_end_utc"))
        writer.writeheader()
        csv_rows = [{**row, "topics": ", ".join(row.get("topics", []))} for row in selection]
        writer.writerows(csv_rows)
        output = buf.getvalue()
    else:
        label = "历史新增 Star（非净增）" if args.source == "history" else "快照净增 Star"
        print(f"\nGitHub Top {args.limit} / 最近 {args.days} 个完整 UTC 日 / {label}")
        print(f"统计范围：{utc_day() - timedelta(days=args.days)} ~ "
              f"{utc_day() - timedelta(days=1)} （包括首尾日期）")
        if selection:
            table_output(selection, limit=args.limit)
        else:
            print("无满足完整时间窗口的数据。先执行 update；若官方历史接口不可用，" 
                  "可以在积累快照后使用 --source snapshot。")
        print(f"可排名仓库 {len(rows)} 个；数据不足 {missing} 个。" )
        return 0
    if args.output:
        out = Path(args.output).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output, encoding="utf-8-sig" if args.format == "csv" else "utf-8")
        print(f"Wrote {out}", file=sys.stderr)
    else:
        print(output)
    return 0


def report_command(args):
    con = open_db(args.db)
    print(f"GitHub 热门项目六周期报告 | UTC 截止日期：{utc_day() - timedelta(days=1)}")
    print("口径：官方日级历史新增 Star（非净增）；仅针对程序已发现的仓库。")
    print("项目详情：包含 GitHub URL、项目简介、语言、Topics 与自动分类；简介保留仓库原文。")
    if con.execute("SELECT COUNT(*) FROM repos").fetchone()[0] == 0:
        print("数据库为空，先运行 python ghtrend.py update")
        return 0
    for days in PERIODS:
        rows, missing = ranked_rows(con, days, source=args.source, language=args.language)
        print(f"\n{'='*72}\n近 {days} 天 | 可排名 {len(rows)} | 不足 {missing}\n{'='*72}")
        if rows:
            detailed_output(rows, limit=args.limit)
        else:
            print("暂无完整窗口数据；可能是官方历史接口不可用，或每日快照尚未积累足够时间。")
    return 0


def watch_command(args):
    con = open_db(args.db)
    if args.watch_action == "add":
        try:
            name = valid_repo_name(args.repo)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        with con:
            con.execute("INSERT OR IGNORE INTO watchlist(full_name) VALUES (?)", (name,))
        print(f"Added {name}; run update to fetch its stats.")
    elif args.watch_action == "remove":
        with con:
            con.execute("DELETE FROM watchlist WHERE full_name=?", (args.repo,))
        print(f"Removed {args.repo} from watchlist (history retained).")
    else:
        names = con.execute("SELECT full_name FROM watchlist ORDER BY full_name").fetchall()
        print("\n".join(r[0] for r in names) if names else "Watchlist empty.")
    return 0


def doctor_command(args):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Database: {args.db}")
    print(f"Token: {'provided' if token else 'not provided (anonymous rate limits apply)'}")
    try:
        limits = HTTPClient(token).api("/rate_limit")
        for kind in ("core", "search"):
            data = limits.get("resources", {}).get(kind, {})
            print(f"{kind}: remaining {data.get('remaining', '?')} / {data.get('limit', '?')}")
    except GitHubError as e:
        print(f"GitHub API unavailable: {e}")
        return 2
    return 0


def make_parser():
    parser = argparse.ArgumentParser(description="GitHub 1/3/7/30/90/180-day Star growth CLI")
    parser.add_argument("--db", default=default_db(), help="SQLite database path (or GHTREND_DB)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("update", help="Discover repositories and download Star histories")
    p.add_argument("--limit", type=int, help="Max repositories per run (default: 20 anonymous, 40 with token)")
    p.add_argument("--language", default="", help="Filter discovery (e.g. python, java, rust)")
    p.add_argument("--skip-trending", action="store_true")
    p.add_argument("--skip-search", action="store_true")
    p.add_argument("--timeout", type=float, default=20)
    p.set_defaults(func=update)
    p = sub.add_parser("top", help="Print one time-window leaderboard")
    p.add_argument("--days", type=int, choices=PERIODS, default=1)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--language", default="")
    p.add_argument("--source", choices=("history", "snapshot"), default="history",
                   help="history = created Stars/day; snapshot = observed net Star change")
    p.add_argument("--format", choices=("table", "json", "csv"), default="table")
    p.add_argument("--output", help="Write JSON/CSV output to a file")
    p.set_defaults(func=top_command)
    p = sub.add_parser("report", help="Print all six time windows")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--language", default="")
    p.add_argument("--source", choices=("history", "snapshot"), default="history")
    p.set_defaults(func=report_command)
    p = sub.add_parser("watch", help="Manage repository watchlist")
    watch = p.add_subparsers(dest="watch_action", required=True)
    for action in ("add", "remove"):
        q = watch.add_parser(action)
        q.add_argument("repo", help="owner/repo")
    watch.add_parser("list")
    p.set_defaults(func=watch_command)
    p = sub.add_parser("doctor", help="Check API connectivity and rate limits")
    p.set_defaults(func=doctor_command)
    return parser


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.command == "update" and args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.command in ("top", "report") and args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.command == "update" and args.timeout <= 0:
        parser.error("--timeout must be > 0")
    if args.command == "top" and args.output and args.format == "table":
        parser.error("--output requires --format json or --format csv")
    if args.command == "update" and args.skip_search and args.skip_trending:
        print("Both discovery sources disabled: only watchlist repositories will be updated.", file=sys.stderr)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
