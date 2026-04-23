# A股数据采集重构设计文档

**日期**: 2026-04-23
**状态**: 已批准

---

## 1. 背景

当前数据采集模块过于复杂，板块、涨停池数据采集经常失败。用户决定简化数据采集，只保留指数和自选股采集，热门板块/个股分析改用 Agent + Tavily 搜索替代。

---

## 2. 目标

- 简化数据采集代码，删除板块/涨停池采集逻辑
- 保持三层架构结构
- 热门板块/个股分析改为报告生成时通过 Tavily 实时搜索

---

## 3. 改动范围

### 3.1 文件改动

| 文件 | 改动内容 | 预计代码变化 |
|------|----------|-------------|
| `scripts/fetchlayer.py` | 删除板块/涨停池采集函数及相关解析函数 | -500行 |
| `scripts/datafoundation.py` | 删除板块/涨停池质量评分逻辑、Tavily 补充生成逻辑 | -200行 |
| `run_report.py` | 删除板块/涨停池采集调用、简化质量校验、调整输出结构 | -150行 |
| `references/daily.md` | 更新第三章/第四章说明为 Tavily 实时搜索 | 微调 |

**预计总删除**: ~850行
**预计剩余**: ~2500行

### 3.2 保留内容

- 三层架构：datafoundation.py → fetchlayer.py → run_report.py
- 其他辅助文件：validate_report.py、push_to_obsidian.py、push_to_feishu.py、scripts/news_storage.py、check_key.py
- 报告模板结构（只调整第三章/第四章生成逻辑描述）

---

## 4. 新数据采集流程

```
run_report.py --mode daily
  │
  ├── fetch_multi_index()
  │     → 上证指数、深证成指、创业板指、沪深300
  │     → 多源并发（腾讯/新浪/东财）
  │     → 交叉验证 + 质量评分
  │
  ├── fetch_multi_watchlist_a(codes)
  │     → A股自选股（config.watchlist.a_shares）
  │     → 多源并发 + 交叉验证
  │
  ├── fetch_multi_watchlist_hk(codes)
  │     → 港股自选股（config.watchlist.hk_stocks）
  │     → 多源并发 + 交叉验证
  │
  └── validate_data()
        → 只校验 indices + watchlist 质量
        → 移除 sectors/zt_pool 校验逻辑
```

---

## 5. 新报告生成流程

```
1. 读取 data_YYYYMMDD.json
     → 指数数据 + 自选股数据

2. Tavily 搜索（总计 10+ 次）：
     │
     ├── 第三章"五大热门板块"（5次）
     │     Query: "今日{板块名}板块涨幅 龙头股 原因分析"
     │     → 每个板块单独搜索，获取代表个股和深度分析素材
     │
     ├── 第四章"五大核心个股"（5次）
     │     Query: "今日{个股名}涨停原因 所属板块 资金动向"
     │     → 每个个股单独搜索，获取板块归属和逻辑推演素材
     │
     └── 宏观/政策新闻（2次）
           Query: "A股今日行情 大盘走势"、"A股政策面利好利空"

3. 生成完整报告
     → 基于指数数据 + 自选股数据 + Tavily 搜索结果
     → 按模板结构填充各章节

4. 推送
     → Obsidian (push_to_obsidian.py)
     → 飞书 (push_to_feishu.py)
```

---

## 6. 新 JSON 输出结构

```json
{
  "date": "2026-04-23",
  "target_date": "2026-04-23",
  "mode": "daily",
  "is_today_trading": true,
  "generated_at": "2026-04-23 15:05:00",

  "indices": [
    {
      "name": "上证指数",
      "code": "000001",
      "price": 3300.50,
      "prev_close": 3290.00,
      "change_pct": 0.32,
      "amount": "3500亿",
      "quality_source": "腾讯",
      "quality_score": 1.0
    },
    // ... 其他指数
  ],

  "watchlist_a": {
    "all": [...],
    "count": 18,
    "gainers": [...],
    "gainers_count": 5,
    "losers": [...],
    "losers_count": 3,
    "flat": [...],
    "gainers_3pct": [...],
    "losers_3pct": [...],
    "top_gainer": {...},
    "top_loser": {...}
  },

  "watchlist_hk": {
    "all": [...],
    "count": 7,
    "gainers": [...],
    "losers": [...],
    // ...
  },

  "quality_report": {
    "passed": true,
    "quality_score": 0.85,
    "source_scores": {
      "indices": 1.0,
      "watchlist_a": 0.9,
      "watchlist_hk": 0.8
    },
    "issues": [],
    "warnings": []
    // 移除 tavily_supplement 字段
  },

  "data_source_status": {
    "indices": "ok",
    "watchlist_a": "ok",
    "watchlist_hk": "ok"
    // 移除 sectors、zt_pool
  }
}
```

---

## 7. 详细删除清单

### 7.1 fetchlayer.py 删除内容

删除以下函数：
- `parse_sina_sector_v2()`
- `parse_sina_sector_v1()`
- `parse_em_sector()`
- `fetch_multi_sectors()`
- `parse_em_zt()`
- `parse_tencent_zt_prices()`
- `fetch_multi_zt_pool()`
- `analyze_hot_sectors()`
- `analyze_hot_stocks()`

保留以下函数：
- `fetch_multi_index()` + 相关解析函数
- `fetch_multi_watchlist_a()` + 相关解析函数
- `fetch_multi_watchlist_hk()` + 相关解析函数
- `fetch_kline()` (周K/月K，用于周报/月报)
- `fetch_multi_async()` (并发请求基础设施)
- `safe_float()`, `safe_int_str()` 等工具函数

### 7.2 datafoundation.py 删除内容

删除以下内容：
- `QualityScorer.score_sectors_data()`
- `QualityScorer.score_zt_pool()`
- `TavilySupplementGenerator` 整个类（不再需要自动生成补充查询）
- `QUERY_TEMPLATES` 中 sectors/zt_pool 相关模板

保留以下内容：
- `DataSourceConfig`, `SourceQuality`, `FetchResult` 数据类
- `CurlKeeper` 类
- `QualityScorer.score_index_data()`, `score_watchlist()`
- `QualityScorer.cross_validate()`
- `normalize_percent()`, `decode_response()` 等工具函数
- `curl_keeper()`, `fetch_with_retry()`, `fetch_multi_async()` 便捷函数

### 7.3 run_report.py 删除内容

删除以下内容：
- `validate_data()` 中 sectors/zt_pool 校验逻辑
- `fetch_daily_data()` 中板块/涨停池采集调用
- `fetch_daily_data()` 中 hot_sectors/hot_stocks 分析调用
- `analyze_hot_sectors()`, `analyze_hot_stocks()` 函数
- 输出结构中 sectors/hot_sectors/zt_pool/hot_stocks 字段
- `tavily_supplement` 相关输出

保留以下内容：
- 指数采集调用
- 自选股采集调用
- 交易日历工具函数
- 基础配置加载逻辑
- watchlist_a/watchlist_hk 分段统计逻辑

### 7.4 references/daily.md 修改内容

更新第三章/第四章说明：

```markdown
## 三、五大热门板块深度解析

**数据来源**：由 Agent 通过 Tavily 实时搜索生成，搜索 Query 如下：
- Query 1: "A股今日热门板块涨幅排名 TOP5"
- Query 2-6: "今日{具体板块名}板块 龙头股 涨幅原因分析"（针对搜索出的每个板块）

...

## 四、五大核心个股追踪

**数据来源**：由 Agent 通过 Tavily 实时搜索生成，搜索 Query 如下：
- Query 1: "A股今日涨停股 涨停原因 概念板块"
- Query 2-6: "今日{具体个股名} 所属板块 资金动向 分析"（针对搜索出的每个个股）
```

---

## 8. 不改动内容

以下文件/功能保持不变：
- `validate_report.py` - 报告验证脚本
- `push_to_obsidian.py` - Obsidian 推送
- `push_to_feishu.py` - 飞书推送
- `scripts/news_storage.py` - 新闻存储
- `check_key.py` - 检查脚本
- `references/validation_meta.md` - 验证元数据
- `references/weekly.md`, `references/monthly.md` - 周报/月报模板
- `config.json` - 配置文件（路径、自选股列表等）

---

## 9. 实施步骤概览

1. 删除 `fetchlayer.py` 中板块/涨停池采集函数
2. 删除 `datafoundation.py` 中板块/涨停池质量评分和 Tavily 补充逻辑
3. 简化 `run_report.py` 数据采集流程和输出结构
4. 更新 `references/daily.md` 模板说明
5. 测试验证新的数据采集流程

详细实施计划将在 `writing-plans` skill 中生成。

---

## 10. 验收标准

- `run_report.py --mode daily` 成功执行，只采集指数+自选股
- 输出 JSON 结构符合第6节定义
- 板块/涨停池相关代码完全删除，无残留引用
- 报告生成流程通过 Tavily 搜索填充第三章/第四章
- 代码总量减少约850行