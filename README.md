# GitHub Trends CLI + Web · 开源项目增长趋势

在原有 Python + SQLite 采集器上增加本地 Web 仪表盘。支持 **3 天 / 7 天 / 1 个月 / 3 个月** 四个排行榜周期，点击任意项目即可查看每日新增 Star 柱状图和移动平均增长曲线。无需 Node.js、npm 或第三方 Python 包。

## Web 仪表盘（推荐）

```bash
# 可选：配置 GitHub Token，提升 API 限额
export GITHUB_TOKEN="你的 GitHub Token"       # Linux / macOS
# PowerShell: $env:GITHUB_TOKEN="你的 GitHub Token"

# 启动本地网站
python web.py
```

浏览器打开 **http://127.0.0.1:8765**。右上角的刷新按钮会启动一次项目发现和历史 Star 采集；采集完成后页面自动刷新。也可以先在终端执行：

```bash
python ghtrend.py doctor
python ghtrend.py update --limit 30
python web.py
```

Web 页面提供：
- 3 天、7 天、30 天、90 天增长排行榜；按新增 Star 排序。
- 项目搜索、分类筛选、编程语言筛选。
- 项目详情抽屉：GitHub URL、简介、Topics、累计 Star、Fork、日均增长。
- 增长时间线：每日新增 Star 柱状图 + 7 日移动平均线（3 天周期使用 3 日移动平均）。
- 数据缺失会明确显示，不会将未采集日期伪装成 0。
- 复用现有 `ghtrend.sqlite3`，不会覆盖或删除历史数据。

默认仅监听 `127.0.0.1`，只供本机访问。不要在不可信网络中直接使用 `--host 0.0.0.0` 暴露服务。

## 项目文件

| 文件 | 作用 |
|---|---|
| `ghtrend.py` | GitHub 数据采集、历史存储和排行榜计算 |
| `web.py` | 标准库 HTTP 服务与 JSON API |
| `web/index.html` | Web 页面结构 |
| `web/styles.css` | 响应式界面样式 |
| `web/app.js` | 排行榜交互与 SVG 增长图表 |
| `ghtrend.sqlite3` | 本地 SQLite 数据库，首次采集后生成 |
| `tests/` | 自动化测试 |

## 命令行模式

原有 CLI 仍然保留，以下命令继续可用：

```bash
python ghtrend.py update --limit 30
python ghtrend.py report
python ghtrend.py top --days 30
python -m unittest discover -s tests -v
```

---


采集和存储层使用 **Python 3.10+ 标准库 + SQLite**；Web 服务与前端也不依赖第三方框架。原有 CLI 保留，支持 **1 / 3 / 7 / 30 / 90 / 180 天** 的命令行统计。

## 1. CLI 采集与初始化（Windows / macOS / Linux）

```bash
# 进入解压后的项目目录
cd github-trends-cli

# 可选，但强烈推荐：配置 GitHub Token，避免 API 频率限制
export GITHUB_TOKEN="你的 GitHub Token"       # Linux / macOS
# PowerShell: $env:GITHUB_TOKEN="你的 GitHub Token"

# 先检查访问 GitHub API 是否正常
python ghtrend.py doctor

# 第一次发现热门项目并补采历史 Star 数据
python ghtrend.py update --limit 30

# 展示全部六个时间窗口的排行榜（含项目 URL、简介、分类、Topics）
python ghtrend.py report
```

没有 Token 也能尝试运行，默认每次只处理最多 **20** 个仓库；匿名请求限制较低（取决于 GitHub 当前规则）。`--limit 30` 建议配置 Token 后使用。

升级说明：如果你已经使用上一版项目和 SQLite 数据库，直接替换程序文件即可。新版会自动为旧数据库增加 Topics 字段，不会删除已有历史 Star 数据；再次运行 `update` 后会刷新项目简介和分类信息。

GitHub Token 推荐使用自己的令牌，并避免写进代码、日志或提交到仓库。对公开仓库的只读接口无需额外的写权限，具体访问规则以 GitHub 官方文档为准。

## 2. 常用命令

```bash
# 单周期排行榜：1、3、7、30、90、180
python ghtrend.py top --days 1
python ghtrend.py top --days 3 --limit 50
python ghtrend.py top --days 7
python ghtrend.py top --days 30
python ghtrend.py top --days 90
python ghtrend.py top --days 180

# 仅看 Python 项目（按项目主要语言过滤本地数据）
python ghtrend.py top --days 7 --language Python

# 每次发现 Java 相关项目（不会删除数据库中其他语言的项目）
python ghtrend.py update --language java --limit 30

# 导出 JSON 和 CSV（包含 URL、简介、自动分类、Topics）
python ghtrend.py top --days 30 --limit 100 --format json --output reports/month.json
python ghtrend.py top --days 7 --limit 100 --format csv --output reports/week.csv

# 监控你指定的仓库（先添加，下次 update 时优先抓取）
python ghtrend.py watch add langchain-ai/langchain
python ghtrend.py watch add microsoft/vscode
python ghtrend.py watch list
python ghtrend.py watch remove microsoft/vscode

# 只更新 watchlist（关闭自动发现）
python ghtrend.py update --skip-search --skip-trending

# 另一种统计口径：快照净增 Star（需要有足够长的每日快照积累）
python ghtrend.py top --days 7 --source snapshot
```

## 3. 数据来自哪里？

1. `https://github.com/trending?since=daily|weekly|monthly`：获得最近的热门候选项目。Trending **没有官方 API**，这里抓取的是官方网页，HTML 结构调整后可能解析失败。
2. `GET /search/repositories`：补充最近创建/更新、受到较多关注的候选项目。GitHub Search API 不提供“最近 N 天新增 Star 排序”，因此它只负责**发现候选**。
3. `GET /repos/{owner}/{repo}/stargazers/history`：GitHub 官方 **Star History API**（API version `2026-03-10`），按周返回每天的新增 Star 桶。一次调用 `per_page=30` 最多获得约 **210 天**的日级数据，理论上首次更新即可计算 180 天榜单。该接口可用性取决于你的网络、访问权限、GitHub 限额与返回数据覆盖范围。
4. 每次 `update` 都保存当前累计 Star / Fork 的 SQLite 快照；之后也能计算**观察到的净增长**（包含取消 Star 的影响）。
5. 同时保存仓库简介、编程语言、GitHub Topics 和项目主页 URL；`report` 会逐个展示项目用途说明，并根据 Topics / 简介关键词进行谨慎的中文分类。分类是辅助归纳，不代表 GitHub 官方分类；简介保留项目维护者提供的原文，不自动翻译。

官方接口文档： https://docs.github.com/en/rest/activity/starring?apiVersion=2026-03-10

## 4. 非常重要的统计口径

- `top` 默认 **`--source history`**：数值是 GitHub 官方日桶的 **新增 Star**，并非现在的累计 Star，也不等于观察期间的净增长（用户可能取消 Star）。
- 使用 **最近 N 个已经完整结束的 UTC 自然日**：例如 UTC 2026-11-12 运行时，1 天是 UTC 11 月 11 日，3 天是 UTC 11 月 9～11 日。今天正在进行中的不计入。
- GitHub 官方明确提示历史接口的周/日分桶边界 **不保证与 UTC 完全重合**，因此历史榜单是以官方日桶为准的近似时间窗口，并非精确到秒的最近 24 小时滚动计数。
- 若历史接口失败、没有覆盖完整时间窗口或数据不足，程序会显示 **“暂无完整窗口数据”**，不会把 7 天伪称 90 天，也不会用 0 冒充缺失值。历史接口不可用时，可坚持每天采集，等快照积累后用 `--source snapshot`。
- `--source snapshot` 是 **净增 Star**，按接近 N 日间隔的两次采样计算，需要起点与终点快照，容许时间偏差 ±12 小时。不要将 `history` 和 `snapshot` 当作完全相同的指标。
- 本项目只统计“**已发现/已监控仓库**”的相对排名，**不是 GitHub 全站的严格 Top 100**。广泛覆盖需要接入 GH Archive 等全站事件数据源。
- Trending 页面能返回的仓库数可能有限；程序不会声称每次一定发现几百个项目。已经监控的项目在下次更新时也可能因 `--limit` 太小而未被刷新。

## 5. 每天自动采集

Linux / macOS 定时任务（`crontab -e`），在早上 08:15（中国时区）执行：

```cron
CRON_TZ=Asia/Shanghai
15 8 * * * cd /absolute/path/github-trends-cli && /usr/bin/python3 ghtrend.py update --limit 30 >> cron.log 2>&1
```

> `CRON_TZ` 在不同 cron 实现中支持不一。确保服务器自身时区正确，或使用 systemd timer / GitHub Actions。`/usr/bin/python3` 改成你的实际 Python 路径。

如使用 GitHub Actions，可参考 `.github/workflows/daily.yml`，它会将 SQLite 数据库和当天报告保存为 Actions Artifact；**不会自动覆盖你的本地数据库**。请在 GitHub 仓库 Settings → Actions 中确认运行权限；新建 Actions 秘钥 `GH_TREND_TOKEN`（非必需但强烈推荐）。该工作流不会自动部署网页，也不需要自己租服务器。

## 6. 常见故障

- `rate limit exceeded`：提供 `GITHUB_TOKEN`、降低 `--limit`、等待官方限额重置。
- `Trending returned no projects`：GitHub 限制抓取或 HTML 改版。Search API 仍可发现仓库。
- `history unavailable (HTTP 403/404/422)`：历史接口不可用或受限，SQLite 累计 Star 快照仍保留；长期可使用 `--source snapshot`。
- `report` 全是“暂无完整窗口数据”：先检查 `python ghtrend.py doctor`、再次执行 `update`，并观察是否提示历史接口错误。
- 只看得到 20 个项目：默认 `--limit` 为匿名 20 / Token 40；扩大 `--limit`，同时考虑官方限额。
- 窗口 180 天缺失：项目早于统计窗口但历史 API 没返回足够早的日桶；程序会标记不足，不捏造数据。

## 7. 测试与文件

```bash
python -m unittest discover -s tests -v
```

| 文件 | 作用 |
|---|---|
| `ghtrend.py` | GitHub 采集、存储和 CLI 统计（无第三方依赖） |
| `web.py` | 本地 Web 服务和 JSON API |
| `web/` | 仪表盘前端资源 |
| `ghtrend.sqlite3` | 首次运行后生成，本地持久化数据（含仓库简介、Topics 与历史 Star） |
| `tests/test_ghtrend.py` | 解析/历史周期/数据库测试 |
| `.github/workflows/daily.yml` | 可选 GitHub Actions 每天运行 |
| `README.md` | 使用说明 |

`ghtrend.sqlite3` 是真实数据库，可长期保留和备份。重装系统后复制它即可恢复历史统计。
