---
name: a-stock-market-report
description: A股市场数据采集与复盘报告生成。腾讯/新浪/东财API多源采集，AI Agent基于模板生成报告，推送Obsidian并校验。
version: 10.0.0
author: Hermes Agent
license: MIT
platforms: [linux, windows]
---

# A股市场复盘报告 Skill

## 核心架构

**三层工作流**：

```
数据采集(run_report.py) → 报告生成(AI Agent + 模板) → 推送验证(push_to_obsidian.py)
```

**文件结构**：

```
a-stock-market-report/
├── SKILL.md              # Skill 定义（本文件）
├── config.json           # 配置（自选股、Obsidian API）
├── run_report.py         # 数据采集脚本
├── push_to_obsidian.py   # Obsidian 推送脚本
├── validate_report.py    # 报告校验脚本
├── scripts/
│   ├── fetchlayer.py     # 多源数据获取层
│   └── datafoundation.py # 基础设施层（curl封装）
└── references/
    ├── daily.md          # 日报模板（AI Agent生成报告的指导模板）
    ├── weekly.md         # 周报模板
    └── monthly.md        # 月报模板
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

### Step 2: 报告生成

AI Agent 基于 `references/daily.md` 模板生成报告：

1. 读取 `references/daily.md` 获取模板结构和要求
2. 解析模板中的元数据区块（SECTIONS、FORBIDDEN等）
3. 结合JSON数据 + Tavily搜索补充（3-5次搜索，直到搜集足够信息）
4. 按模板结构逐章节生成完整Markdown报告

**模板的作用**：指导AI Agent生成报告的格式、章节结构、字数要求，**不是手动撰写**。

### Step 3: 校验

```bash
python validate_report.py <report.md> --type daily
```

验证脚本动态读取模板元数据，检查：
- 六章节完整性
- 表格行数达标
- 字数达标
- 无禁止占位符

### Step 4: 推送

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
    "data_dir": "/opt/data/market-reports/data",
    "reports_dir": "/opt/data/market-reports/reports"
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

## 数据质量评分

当某数据源评分<0.5时，JSON输出包含 `tavily_queries` 字段，Agent应自动补充搜索。

---

**关键**：模板文件（references/*.md）是AI Agent生成报告的指导模板，包含完整结构要求和元数据。Agent读取模板后自动生成报告，**非手动撰写**。