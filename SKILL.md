---
name: a-stock-market-report
description: A股市场数据采集与复盘报告生成。腾讯/新浪/东财API多源采集，AI Agent基于模板生成报告，新闻数据持久化存储，推送Obsidian并校验。
version: 11.0.0
author: Hermes Agent
license: MIT
platforms: [linux, windows]
---

# A股市场复盘报告 Skill

## 核心架构

**三层工作流**：

```
数据采集(run_report.py) → 新闻存储(news_storage.py) → 报告生成(AI Agent + 模板) → 推送验证(push_to_obsidian.py)
```

**文件结构**：

```
a-stock-market-report/
├── SKILL.md              # Skill 定义（本文件）
├── config.json           # 配置（自选股、Obsidian API）
├── run_report.py         # 数据采集脚本
├── push_to_obsidian.py   # Obsidian 推送脚本
├── validate_report.py    # 报告校验脚本
├── output/               # 统一输出目录（新增）
│   ├── data/             # 市场数据 JSON
│   │   ├── data_20260422.json
│   │   └── ...
│   ├── news/             # 新闻数据（新增）
│   │   ├── daily/        # 日报新闻
│   │   │   ├── news_20260422.json
│   │   │   └── ...
│   │   ├── weekly/       # 周报新闻聚合
│   │   │   ├── news_2026-W17.json
│   │   │   └── ...
│   │   └── monthly/      # 月报新闻聚合
│   │   │   ├── news_2026-04.json
│   │   │   └── ...
│   └── reports/          # 报告文件
│       ├── daily/
│       │   ├── report_2026-04-22.md
│       │   └── ...
│       ├── weekly/
│       │   ├── report_2026-W17.md
│       │   └── ...
│       └── monthly/
│           ├── report_2026-04.md
│           └── ...
├── scripts/
│   ├── fetchlayer.py     # 多源数据获取层
│   ├── datafoundation.py # 基础设施层（curl封装）
│   └── news_storage.py   # 新闻存储与聚合（新增）
└── references/
    ├── daily.md          # 日报模板（AI Agent生成报告的指导模板）
    ├── weekly.md         # 周报模板
    ├── monthly.md        # 月报模板
    └── validation_meta.md # 统一验证元数据（禁止模式、类型检测规则）
```

## 执行流程

### Step 1: 数据采集

```bash
cd {skill_dir}
python run_report.py <日期> --mode daily
```

**输出**：JSON数据文件（stdout + data_YYYYMMDD.json）

**多源采集**：
- 指数：腾讯/新浪/东财三源并发，交叉验证
- 板块：新浪v2 + 东财push2delay两源
- 涨停池：东财主源
- 自选股：腾讯/新浪/东财三源

### Step 2: 新闻数据存储（新增）

日报生成时，Tavily 搜索结果自动保存到 `output/news/daily/` 目录：

```
output/news/daily/news_YYYYMMDD.json
```

新闻数据格式：
```json
{
  "date": "2026-04-22",
  "search_queries": ["A股 今日行情...", ...],
  "results": [{"query": "...", "items": [...]}],
  "meta": {"total_items": 15, "credit_used": 3}
}
```

**好处**：
- 周报/月报复用日报新闻，节省 Tavily credits
- 数据可追溯，便于回溯分析

### Step 3: 报告生成

AI Agent 基于 `references/daily.md` 模板生成报告：

1. 读取 `references/daily.md` 获取模板结构和要求
2. 解析模板中的元数据区块（SECTIONS、FORBIDDEN等）
3. 结合JSON数据 + Tavily搜索补充（日报：3-5次搜索）
4. **新闻数据自动保存到 output/news/daily/ 目录**
5. 按模板结构逐章节生成完整Markdown报告

**模板的作用**：指导AI Agent生成报告的格式、章节结构、字数要求，**不是手动撰写**。

### Step 4: 周报/月报新闻复用（新增）

周报/月报生成时优先复用已存储的新闻数据：

| 报告类型 | 新闻来源 | 补充搜索次数 |
|---------|---------|------------|
| 周报 | 本周日报聚合 | 2-3次（仅补充遗漏） |
| 月报 | 本月日报+周报聚合 | 3-5次（仅补充遗漏） |

复用流程：
```
1. 读取 output/news/daily/ 中本周/本月的新闻文件
2. 聚合所有新闻，去重（基于 URL）
3. 检查覆盖率（是否有重大事件遗漏）
4. 如有遗漏，补充搜索
5. 保存聚合结果到 output/news/weekly/ 或 output/news/monthly/
```

### Step 5: 校验

```bash
python validate_report.py <report.md> --type daily
```

验证脚本动态读取模板元数据，检查：
- 六章节完整性
- 表格行数达标
- 字数达标
- 无禁止占位符

### Step 6: 推送

```bash
python push_to_obsidian.py push --file "2026-04-22_日报.md" --content-file report.md
```

推送后必须GET验证文件存在。

## 模板元数据

每个模板文件（daily.md/weekly.md/monthly.md）末尾包含元数据区块：

```markdown
<!--
SECTIONS_DAILY:
一、市场全景概览|required|table|min_rows=4
二、重要财经要闻|required|list|min_items=3
...
SECTIONS_DAILY:END

FORBIDDEN:
由AI深度分析后补充
待补充
暂无数据
FORBIDDEN:END
-->
```

**修改规则只需编辑模板文件，验证脚本自动适配**。

## 配置说明

`config.json` 关键字段：

```json
{
  "paths": {
    "output_dir": "output",
    "data_dir": "output/data",
    "news_dir": "output/news",
    "reports_dir": "output/reports"
  },
  "obsidian": {
    "api_url": "https://obsidian-api.xxx:5000",
    "api_key": "完整64字符密钥",
    "vault_path": "/01_投资研究/每日复盘"
  },
  "watchlist": {
    "a_shares": ["000738", "000960", ...],
    "hk_stocks": ["00700", "01810", ...]
  }
}
```

**注意**：
- API Key 必须完整版本，不能用缩短版
- 文件读写统一UTF-8编码

## 常见问题

1. **GBK编码问题**：腾讯/新浪API返回GBK，脚本已处理
2. **Windows终端GBK**：输出emoji会报错，已替换为ASCII
3. **push_to_obsidian.py编码**：读取文件需指定UTF-8
4. **板块涨跌幅格式**：已统一为百分比，无需再×100

## 新闻数据存储与复用

### 目录结构

```
output/news/
├── daily/     # 日报新闻（每日 Tavily 搜索结果）
├── weekly/    # 周报新闻聚合（复用日报 + 补充）
└── monthly/   # 月报新闻聚合（复用日报+周报 + 补充）
```

### 存储时机

- **日报生成时自动存储**：Tavily 搜索结果保存到 `output/news/daily/news_YYYYMMDD.json`
- **周报生成时聚合**：读取本周日报，聚合保存到 `output/news/weekly/news_YYYY-WXX.json`
- **月报生成时聚合**：读取本月日报+周报，聚合保存到 `output/news/monthly/news_YYYY-MM.json`

### 复用策略

| 报告类型 | 复用来源 | 补充搜索 |
|---------|---------|---------|
| 周报 | 本周日报（周一至周五） | 2-3次（仅遗漏事件） |
| 月报 | 本月日报 + 本月周报 | 3-5次（仅遗漏事件） |

### 新闻数据格式

```json
{
  "date": "2026-04-22",
  "generated_at": "2026-04-22 16:15:00",
  "search_queries": ["A股 今日行情...", ...],
  "results": [
    {
      "query": "...",
      "items": [{"title": "...", "url": "...", "content": "..."}]
    }
  ],
  "meta": {"total_items": 15, "credit_used": 3}
}
```

### 使用脚本

```python
# scripts/news_storage.py
from news_storage import save_daily_news, load_daily_news
from news_storage import aggregate_weekly_news, aggregate_monthly_news

# 日报：保存新闻
save_daily_news("2026-04-22", search_queries, results)

# 周报：聚合新闻（自动复用日报）
weekly_news = aggregate_weekly_news("2026-04-22")

# 月报：聚合新闻（自动复用日报+周报）
monthly_news = aggregate_monthly_news("2026-04")
```

---

## 数据质量评分

当某数据源评分<0.5时，JSON输出包含 `tavily_queries` 字段，Agent应自动补充搜索。

---

**关键**：模板文件（references/*.md）是AI Agent生成报告的指导模板，包含完整结构要求和元数据。Agent读取模板后自动生成报告，**非手动撰写**。

## 依赖说明

**Python 版本**：3.8+

**标准库依赖**：
- `requests`：HTTP 请求（数据采集）
- `json`：数据解析
- `re`：正则表达式（验证脚本）

**可选依赖**：
- 无第三方依赖，全部使用 Python 标准库

---