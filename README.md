# GitHub Trends CLI · 六周期开源项目热度统计

只用 **Python 3.10+ 标准库 + SQLite**，命令行统计 GitHub 热门项目的新增 Star，支持 **1 / 3 / 7 / 30 / 90 / 180 天**。无需安装第三方包，不需要网页或部署服务器。

## 1. 启动（Windows / macOS / Linux）

```bash
cd github-trends-cli

# 可选：配置 GitHub Token，避免 API 频率限制
export GITHUB_TOKEN="你的 GitHub Token"       # Linux / macOS
# PowerShell: $env:GITHUB_TOKEN="你的 GitHub Token"

python ghtrend.py doctor
python ghtrend.py update --limit 30
python ghtrend.py report
```

没有 Token 也能尝试运行，默认每次最多处理 20 个仓库；匿名请求限制较低。`--limit 30` 建议配置 Token 后使用。

如果你已使用旧版数据库，替换程序文件即可。程序会自动为旧数据库增加 Topics 字段，不会删除已有历史 Star 数据；再次运行 `update` 会刷新项目简介和分类信息。

## 2. 常用命令

```bash
# 六个周期的排行榜
python ghtrend.py top --days 1
python ghtrend.py top --days 3
python ghtrend.py top --days 7
python ghtrend.py top --days 30
python ghtrend.py top --days 90
python ghtrend.py top --days 180

# 语言过滤
python ghtrend.py top --days 7 --language Python

# 发现 Java 相关项目
python ghtrend.py update --language java --limit 30

# 导出 JSON / CSV
python ghtrend.py top --days 30 --limit 100 --format json --output reports/month.json
python ghtrend.py top --days 7 --limit 100 --format csv --output reports/week.csv

# 管理自选监控仓库
python ghtrend.py watch add langchain-ai/langchain
python ghtrend.py watch add microsoft/vscode
python ghtrend.py watch list
python ghtrend.py watch remove microsoft/vscode

# 只更新 watchlist
python ghtrend.py update --skip-search --skip-trending

# 另一种口径：快照净增 Star
python ghtrend.py top --days 7 --source snapshot
```

## 3. 数据来源

1. GitHub Trending 页面：发现近期热门候选。Trending 没有官方 API，HTML 结构变化可能影响解析。
2. GitHub Search API：补充候选项目。Search API 不提供“最近 N 天新增 Star 排序”，只负责发现候选。
3. GitHub Star History API：按周返回每天新增 Star 桶。一次调用 `per_page=30` 最多覆盖约 210 天，首次更新即可尝试构建 180 天榜单。接口可用性取决于网络、权限、限额和返回数据范围。
4. 每次更新保存当前 Star / Fork 快照；可计算观察期间净增长（包含取消 Star 的影响）。
5. 保存项目简介、编程语言、Topics 和 URL；报告根据 Topics / 简介关键词谨慎归类。简介保留维护者原文，不自动翻译。

官方文档：https://docs.github.com/en/rest/activity/starring?apiVersion=2026-03-10

## 4. 统计口径与限制

- `top` 默认 `--source history`：统计 GitHub 历史日桶新增 Star，不是累计 Star，也不等于净增长（用户可能取消 Star）。
- 使用最近 N 个已完整结束的 UTC 自然日，不包含当天未结束的数据。
- GitHub 提示历史接口的日桶边界不保证与 UTC 完全重合，因此是近似时间窗口，不是精确到秒的滚动窗口。
- 历史接口失败或数据覆盖不足时显示“暂无完整窗口数据”，不伪造数值。可持续每日采集，之后用 `--source snapshot`。
- `--source snapshot` 计算净增 Star，需要有足够间隔的起点和终点快照。
- 本项目统计已发现/已监控仓库，不是 GitHub 全站严格 Top 100。要扩大覆盖面可接入 GH Archive。

## 5. 每日自动采集

Linux / macOS 可用 `crontab -e`，例如北京时间 08:15：

```cron
CRON_TZ=Asia/Shanghai
15 8 * * * cd /absolute/path/github-trends-cli && /usr/bin/python3 ghtrend.py update --limit 30 >> cron.log 2>&1
```

也可使用 `.github/workflows/daily.yml`。GitHub Actions 会将 SQLite 数据库和日报保存为 Artifact，不会覆盖你的本地数据库。建议在仓库 Secrets 中配置 `GH_TREND_TOKEN`。

## 6. 常见问题

- `rate limit exceeded`：配置 `GITHUB_TOKEN`、降低 `--limit` 或等待限额重置。
- Trending 解析不到项目：可能是 GitHub 限制或页面结构调整；Search API 仍可发现仓库。
- Star history 返回 403/404/422：接口可能不可用或受限；快照数据仍会保留。
- 180 天数据不足：历史接口没有返回足够早的数据；程序会标记缺失，不会用较短周期冒充。

## 7. 测试

```bash
python -m unittest discover -s tests -v
```

## 文件

| 文件 | 用途 |
|---|---|
| `ghtrend.py` | CLI 主程序，无第三方依赖 |
| `tests/test_ghtrend.py` | 自动化测试 |
| `.github/workflows/daily.yml` | 可选每日 GitHub Actions 采集 |
| `ghtrend.sqlite3` | 首次运行后生成的本地数据库，不应提交 |
| `README.md` | 使用说明 |

MIT License。
