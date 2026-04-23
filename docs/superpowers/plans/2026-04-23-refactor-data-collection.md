# Data Collection Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify data collection to only indices + watchlist, remove ~850 lines of sectors/涨停池 code.

**Architecture:** Keep three-layer structure (datafoundation → fetchlayer → run_report), delete板块/涨停池 collection functions, simplify quality validation and JSON output.

**Tech Stack:** Python 3, curl subprocess, concurrent.futures, json

---

## File Structure

| File | Change | Purpose |
|------|--------|---------|
| `scripts/fetchlayer.py` | Modify (delete ~500 lines) | Remove sectors/涨停池 functions, keep index/watchlist |
| `scripts/datafoundation.py` | Modify (delete ~200 lines) | Remove板块/涨停池 quality scoring, Tavily supplement |
| `run_report.py` | Modify (delete ~150 lines) | Simplify data flow and output structure |
| `references/daily.md` | Modify | Update chapter 3/4 descriptions for Tavily search |

---

## Task 1: Delete Sector Functions in fetchlayer.py

**Files:**
- Modify: `scripts/fetchlayer.py` (delete lines 414-648)

**Functions to delete:**
- `parse_sina_sector_v2()` (lines 414-475)
- `parse_sina_sector_v1()` (lines 477-479)
- `parse_em_sector()` (lines 482-504)
- `fetch_multi_sectors()` (lines 507-648)

- [ ] **Step 1: Delete parse_sina_sector_v2 function**

Locate lines 414-475 in `scripts/fetchlayer.py`, delete entire function:

```python
# DELETE THIS BLOCK (lines 414-475):
def parse_sina_sector_v2(content: str) -> List[Dict]:
    """解析新浪 newFLJK.php?param=class (v2) 板块响应"""
    # ... entire function body
```

- [ ] **Step 2: Delete parse_sina_sector_v1 function**

Locate lines 477-479, delete:

```python
# DELETE THIS BLOCK (lines 477-479):
def parse_sina_sector_v1(content: str) -> List[Dict]:
    """解析新浪 newFLJK.php?param=class&type=2 (v1) 板块响应"""
    return parse_sina_sector_v2(content)
```

- [ ] **Step 3: Delete parse_em_sector function**

Locate lines 482-504, delete entire function.

- [ ] **Step 4: Delete fetch_multi_sectors function**

Locate lines 507-648, delete entire function (approximately 140 lines).

- [ ] **Step 5: Verify no remaining references to deleted functions**

Search for any remaining calls to deleted functions:

```bash
cd C:/Users/玄阳主/Desktop/fsdownload/a-stock-market-report
grep -n "fetch_multi_sectors" scripts/fetchlayer.py run_report.py
grep -n "parse_sina_sector" scripts/fetchlayer.py
grep -n "parse_em_sector" scripts/fetchlayer.py
```

Expected: No results (all references removed)

- [ ] **Step 6: Commit fetchlayer.py sector cleanup**

```bash
git add scripts/fetchlayer.py
git commit -m "$(cat <<'EOF'
refactor: remove sector collection functions from fetchlayer

Delete:
- parse_sina_sector_v2/v1
- parse_em_sector
- fetch_multi_sectors (~140 lines)
EOF
)"
```

---

## Task 2: Delete 涨停池 Functions in fetchlayer.py

**Files:**
- Modify: `scripts/fetchlayer.py` (delete lines 654-828)

**Functions to delete:**
- `parse_em_zt()` (lines 654-676)
- `parse_tencent_zt_prices()` (lines 679-733)
- `fetch_multi_zt_pool()` (lines 736-828)

- [ ] **Step 1: Delete parse_em_zt function**

Locate lines 654-676, delete entire function.

- [ ] **Step 2: Delete parse_tencent_zt_prices function**

Locate lines 679-733, delete entire function (~55 lines).

- [ ] **Step 3: Delete fetch_multi_zt_pool function**

Locate lines 736-828, delete entire function (~90 lines).

- [ ] **Step 4: Delete analyze_hot_sectors and analyze_hot_stocks functions**

Locate lines 1444-1478 (at end of file), delete both functions:

```python
# DELETE THIS BLOCK:
def analyze_hot_sectors(sectors: List[Dict], top_n: int = 5) -> List[Dict]:
    # ... entire function

def analyze_hot_stocks(zt_pool: List[Dict], top_n: int = 5) -> List[Dict]:
    # ... entire function
```

- [ ] **Step 5: Verify no remaining references**

```bash
grep -n "fetch_multi_zt_pool" scripts/fetchlayer.py run_report.py
grep -n "parse_em_zt" scripts/fetchlayer.py
grep -n "analyze_hot_sectors\|analyze_hot_stocks" scripts/fetchlayer.py run_report.py
```

Expected: No results

- [ ] **Step 6: Commit 涨停池 cleanup**

```bash
git add scripts/fetchlayer.py
git commit -m "$(cat <<'EOF'
refactor: remove 涨停池 collection functions from fetchlayer

Delete:
- parse_em_zt
- parse_tencent_zt_prices  
- fetch_multi_zt_pool
- analyze_hot_sectors/analyze_hot_stocks (~200 lines total)
EOF
)"
```

---

## Task 3: Update fetchlayer.py Imports and Entry Test

**Files:**
- Modify: `scripts/fetchlayer.py` (imports section and __main__ block)

- [ ] **Step 1: Remove deleted functions from __main__ block**

Locate lines 1485-1497, update the test list:

```python
# REPLACE the funcs list in __main__ block:
    funcs = [
        "fetch_multi_index",
        "fetch_multi_watchlist_a", "fetch_multi_watchlist_hk",
        "fetch_kline",
    ]
# Remove: fetch_multi_sectors, fetch_multi_zt_pool, analyze_hot_sectors, analyze_hot_stocks
```

- [ ] **Step 2: Commit fetchlayer.py final cleanup**

```bash
git add scripts/fetchlayer.py
git commit -m "refactor: update fetchlayer test list, remove deleted function names"
```

---

## Task 4: Delete Sector/涨停池 Quality Scoring in datafoundation.py

**Files:**
- Modify: `scripts/datafoundation.py`

- [ ] **Step 1: Delete QualityScorer.score_sectors_data method**

Locate lines 553-622, delete entire method:

```python
# DELETE THIS BLOCK (lines 553-622):
def score_sectors_data(
    self,
    data: List[Dict[str, Any]],
    source: str,
) -> SourceQuality:
    # ... entire method body (~70 lines)
```

- [ ] **Step 2: Delete QualityScorer.score_zt_pool method**

Locate lines 674-731, delete entire method (~57 lines).

- [ ] **Step 3: Delete SECTOR_REQUIRED_FIELDS and ZT_POOL_REQUIRED_FIELDS constants**

Locate lines 485-488, delete:

```python
# DELETE THIS BLOCK:
SECTOR_REQUIRED_FIELDS: Tuple[str, ...] = ("name", "raw_change")
ZT_POOL_REQUIRED_FIELDS: Tuple[str, ...] = ("name", "code", "zt_reason")
```

- [ ] **Step 4: Commit datafoundation.py quality scoring cleanup**

```bash
git add scripts/datafoundation.py
git commit -m "$(cat <<'EOF'
refactor: remove sector/涨停池 quality scoring from datafoundation

Delete:
- QualityScorer.score_sectors_data
- QualityScorer.score_zt_pool
- SECTOR_REQUIRED_FIELDS, ZT_POOL_REQUIRED_FIELDS constants
EOF
)"
```

---

## Task 5: Delete TavilySupplementGenerator in datafoundation.py

**Files:**
- Modify: `scripts/datafoundation.py` (lines 922-1037)

- [ ] **Step 1: Delete TavilySupplementGenerator class**

Locate lines 922-1037, delete entire class (~115 lines):

```python
# DELETE THIS BLOCK (lines 922-1037):
class TavilySupplementGenerator:
    """
    当数据质量不达标时，生成 Tavily 补充搜索查询。
    ...
    """
    QUERY_TEMPLATES: Dict[str, List[str]] = {...}
    
    def __init__(self) -> None:
        ...
    
    def needs_supplement(self, quality: SourceQuality, threshold: float = 0.5) -> bool:
        ...
    
    def generate_queries(self, data_type: str, date: str, quality: SourceQuality) -> List[str]:
        ...
```

- [ ] **Step 2: Delete QUERY_TEMPLATES for sectors/zt_pool from anywhere**

Search and verify no remaining QUERY_TEMPLATES references:

```bash
grep -n "QUERY_TEMPLATES\|TavilySupplement" scripts/datafoundation.py
```

Expected: No results after deletion

- [ ] **Step 3: Update __main__ test block**

Locate lines 1365-1419, remove TavilySupplementGenerator test:

```python
# DELETE this section from __main__ (around lines 1400-1404):
    # 5. TavilySupplementGenerator
    print("5. TavilySupplementGenerator:")
    generator = TavilySupplementGenerator()
    print(f"   needs_supplement(0.8): {generator.needs_supplement(sq, 0.5)}")
    queries = generator.generate_queries("index", "2026-04-21", sq_index)
    print(f"   生成查询: {queries}\n")
```

- [ ] **Step 4: Commit datafoundation.py Tavily cleanup**

```bash
git add scripts/datafoundation.py
git commit -m "$(cat <<'EOF'
refactor: remove TavilySupplementGenerator from datafoundation

Delete entire class (~115 lines) - no longer needed
since sectors/涨停池 collection removed
EOF
)"
```

---

## Task 6: Simplify validate_data in run_report.py

**Files:**
- Modify: `run_report.py` (lines 142-234)

- [ ] **Step 1: Remove sectors/zt_pool parameters and checks from validate_data**

Replace the entire `validate_data` function (lines 142-234) with simplified version:

```python
def validate_data(indices: list = None, 
                  watchlist_a: list = None, watchlist_hk: list = None,
                  quality_info: dict = None) -> dict:
    """
    数据质量校验，返回结构化的 quality_report。
    
    只校验指数和自选股，移除板块/涨停池校验。
    """
    if indices is None: indices = []
    if watchlist_a is None: watchlist_a = []
    if watchlist_hk is None: watchlist_hk = []
    
    issues = []
    warnings = []
    source_scores = {}
    
    # === 指数检查 ===
    if not indices:
        issues.append("指数数据获取失败")
        source_scores['indices'] = 0.0
    else:
        source_scores['indices'] = 1.0
    
    # === 自选股检查 ===
    if not watchlist_a:
        warnings.append("A股自选股数据为空")
        source_scores['watchlist_a'] = 0.0
    elif len(watchlist_a) < 5:
        warnings.append(f"A股自选股数据偏少（仅 {len(watchlist_a)} 只）")
        source_scores['watchlist_a'] = 0.7
    else:
        source_scores['watchlist_a'] = 1.0
    
    if not watchlist_hk:
        warnings.append("港股自选股数据为空")
        source_scores['watchlist_hk'] = 0.0
    elif len(watchlist_hk) < 3:
        warnings.append(f"港股自选股数据偏少（仅 {len(watchlist_hk)} 只）")
        source_scores['watchlist_hk'] = 0.7
    else:
        source_scores['watchlist_hk'] = 1.0
    
    # === 综合评分 ===
    if source_scores:
        overall = sum(source_scores.values()) / len(source_scores)
    else:
        overall = 0.0
    
    passed = overall >= 0.5 and len(issues) == 0
    
    return {
        'passed': passed,
        'quality_score': overall,
        'source_scores': source_scores,
        'issues': issues,
        'warnings': warnings,
    }
```

- [ ] **Step 2: Commit validate_data simplification**

```bash
git add run_report.py
git commit -m "$(cat <<'EOF'
refactor: simplify validate_data to only check indices + watchlist

Remove sectors/zt_pool validation logic (~90 lines simplified)
Remove tavily_supplement generation
EOF
)"
```

---

## Task 7: Update fetch_daily_data in run_report.py

**Files:**
- Modify: `run_report.py` (lines 315-449)

- [ ] **Step 1: Remove sector/涨停池 collection calls**

Locate lines 335-339 in `fetch_daily_data`, delete these calls:

```python
# DELETE THIS BLOCK:
    sectors, sector_qualities = fetch_multi_sectors()
    print(f"  板块: {len(sectors)} 个", file=sys.stderr)
    
    zt_pool, zt_qualities = fetch_multi_zt_pool()
    print(f"  涨停: {len(zt_pool)} 只", file=sys.stderr)
```

- [ ] **Step 2: Update validate_data call**

Locate line 350, change from:

```python
quality_report = validate_data(sectors, zt_pool, indices)
```

To:

```python
quality_report = validate_data(indices, a_stocks, hk_stocks)
```

- [ ] **Step 3: Delete hot_sectors/hot_stocks analysis calls**

Locate lines 363-364, delete:

```python
# DELETE THIS BLOCK:
    hot_sectors = analyze_hot_sectors(sectors)
    hot_stocks = analyze_hot_stocks(zt_pool)
```

- [ ] **Step 4: Update output structure**

Locate lines 393-441, replace the output dictionary construction:

```python
# REPLACE the output dict (lines 393-441) with:
    output = {
        "date": date_str,
        "target_date": target_date,
        "mode": "daily",
        "is_today_trading": is_today_trading,
        "generated_at": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "akshare_ready": AKSHARE_READY,
        "quality_report": quality_report,
        "indices": indices,
        "watchlist_a": {
            "all": a_stocks,
            "count": len(a_stocks),
            "gainers": a_gainers,
            "gainers_count": len(a_gainers),
            "losers": a_losers,
            "losers_count": len(a_losers),
            "flat": a_flat,
            "flat_count": len(a_flat),
            "gainers_3pct": a_gainers_3pct,
            "losers_3pct": a_losers_3pct,
            "top_gainer": a_gainers[0] if a_gainers else None,
            "top_loser": a_losers[0] if a_losers else None,
        },
        "watchlist_hk": {
            "all": hk_stocks,
            "count": len(hk_stocks),
            "gainers": hk_gainers,
            "gainers_count": len(hk_gainers),
            "losers": hk_losers,
            "losers_count": len(hk_losers),
            "flat": hk_flat,
            "flat_count": len(hk_flat),
            "gainers_3pct": hk_gainers_3pct,
            "losers_3pct": hk_losers_3pct,
            "top_gainer": hk_gainers[0] if hk_gainers else None,
            "top_loser": hk_losers[0] if hk_losers else None,
        },
        "data_source_status": {
            "indices": "ok" if indices else "failed",
            "watchlist_a": "ok" if a_stocks else "failed",
            "watchlist_hk": "ok" if hk_stocks else "failed",
        },
    }
# Removed: sectors, hot_sectors, zt_pool, hot_stocks fields
```

- [ ] **Step 5: Commit fetch_daily_data cleanup**

```bash
git add run_report.py
git commit -m "$(cat <<'EOF'
refactor: simplify fetch_daily_data output structure

Remove sectors/涨停池 collection calls
Remove hot_sectors/hot_stocks analysis
Update JSON output to only include indices + watchlist
EOF
)"
```

---

## Task 8: Update fetchlayer Imports in run_report.py

**Files:**
- Modify: `run_report.py` (lines 30-42)

- [ ] **Step 1: Remove deleted function imports**

Locate lines 30-42, update the import statement:

```python
# REPLACE the import block:
try:
    from fetchlayer import (
        fetch_multi_index,
        fetch_multi_watchlist_a,
        fetch_multi_watchlist_hk,
    )
    from datafoundation import SourceQuality
    FETCHLAYER_READY = True
except ImportError as e:
    print(f"⚠️ fetchlayer 导入失败: {e}", file=sys.stderr)
    FETCHLAYER_READY = False

# Removed imports: fetch_multi_sectors, fetch_multi_zt_pool
```

- [ ] **Step 2: Commit import cleanup**

```bash
git add run_report.py
git commit -m "refactor: remove deleted function imports from run_report"
```

---

## Task 9: Delete analyze_hot Functions in run_report.py

**Files:**
- Modify: `run_report.py` (lines 238-255)

- [ ] **Step 1: Delete analyze_hot_sectors function**

Locate lines 238-245, delete entire function:

```python
# DELETE THIS BLOCK:
def analyze_hot_sectors(sectors: List[Dict], top_n: int = 5) -> List[Dict]:
    """热门板块排序（涨幅 TOP N）"""
    sorted_sectors = sorted(
        [s for s in sectors if s.get('change', 0) != 0],
        key=lambda x: abs(x.get('change', 0)),
        reverse=True
    )
    return sorted_sectors[:top_n]
```

- [ ] **Step 2: Delete analyze_hot_stocks function**

Locate lines 247-255, delete entire function.

- [ ] **Step 3: Commit function deletion**

```bash
git add run_report.py
git commit -m "refactor: remove analyze_hot_sectors/analyze_hot_stocks from run_report"
```

---

## Task 10: Update Weekly/Monthly Data Functions

**Files:**
- Modify: `run_report.py` (lines 452-553)

- [ ] **Step 1: Remove sector/涨停池 calls from fetch_weekly_data**

Locate lines 472-475, delete:

```python
# DELETE THIS BLOCK:
    sectors, _ = fetch_multi_sectors()
    zt_pool, _ = fetch_multi_zt_pool()
```

- [ ] **Step 2: Update validate_data call in fetch_weekly_data**

Locate line 478, change:

```python
quality_report = validate_data(sectors, zt_pool, indices)
```

To:

```python
quality_report = validate_data(indices, a_stocks)
```

- [ ] **Step 3: Delete hot analysis in fetch_weekly_data**

Locate lines 479-480, delete:

```python
hot_sectors = analyze_hot_sectors(sectors)
hot_stocks = analyze_hot_stocks(zt_pool)
```

- [ ] **Step 4: Update weekly output structure**

Locate lines 482-494, replace output dict:

```python
# REPLACE output dict in fetch_weekly_data:
    output = {
        "date": date_str,
        "target_date": target_date,
        "mode": "weekly",
        "generated_at": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "quality_report": quality_report,
        "indices": indices,
        "watchlist_a": {"all": a_stocks},
    }
# Removed: sectors, hot_sectors, zt_pool, hot_stocks
```

- [ ] **Step 5: Apply same changes to fetch_monthly_data**

Repeat steps 1-4 for `fetch_monthly_data` function (lines 504-553).

- [ ] **Step 6: Commit weekly/monthly cleanup**

```bash
git add run_report.py
git commit -m "$(cat <<'EOF'
refactor: simplify weekly/monthly data functions

Remove sectors/涨停池 collection
Update output structure to match daily format
EOF
)"
```

---

## Task 11: Update references/daily.md Template

**Files:**
- Modify: `references/daily.md`

- [ ] **Step 1: Update Chapter 3 description**

Locate lines 97-117, add Tavily search explanation:

```markdown
## 三、五大热门板块深度解析

**数据来源**：由 Agent 通过 Tavily 实时搜索生成。

**搜索策略**：
- Query 1: "A股今日热门板块涨幅排名 TOP5"
- Query 2-6: "今日{具体板块名}板块 龙头股 涨幅原因分析"（针对每个板块单独搜索）

**板块筛选逻辑**：从投资经理视角综合考量，非单纯按涨跌幅排序。权重分配：
- 板块热度（20%）：市场讨论度、新闻曝光度、社交媒体热度
- 资金关注度（20%）：板块资金净流入、成交量放大程度
- 涨跌幅（40%）：当日涨幅绝对值
- 持续性（20%）：连续上涨天数、前期表现、趋势强度
```

- [ ] **Step 2: Update Chapter 4 description**

Locate lines 119-136, add Tavily search explanation:

```markdown
## 四、五大核心个股追踪

**数据来源**：由 Agent 通过 Tavily 实时搜索生成。

**搜索策略**：
- Query 1: "A股今日涨停股 涨停原因 概念板块"
- Query 2-6: "今日{具体个股名} 所属板块 资金动向 分析"（针对每个个股单独搜索）

**选股逻辑**：从全市场热点个股中筛选，热门板块权重60% + 个股权重40%，剔除新股（上市不足30天）。
```

- [ ] **Step 3: Update Tavily search section**

Locate lines 194-246, update the search requirements:

```markdown
## Tavily 研究策略

### 日报必搜 Query（更新）

**总搜索次数：12次（10次章节搜索 + 2次宏观搜索）**

```
# 第三章：热门板块（5次）
Query 1: "A股今日热门板块涨幅排名 TOP5"
Query 2: "今日{板块1}板块 龙头股 原因"
Query 3: "今日{板块2}板块 龙头股 原因"
Query 4: "今日{板块3}板块 龙头股 原因"
Query 5: "今日{板块4}板块 龙头股 原因"

# 第四章：核心个股（5次）
Query 6: "A股今日涨停股 涨停原因"
Query 7: "今日{个股1} 所属板块 资金动向"
Query 8: "今日{个股2} 所属板块 资金动向"
Query 9: "今日{个股3} 所属板块 资金动向"
Query 10: "今日{个股4} 所属板块 资金动向"

# 宏观新闻（2次）
Query 11: "A股今日行情 大盘走势"
Query 12: "A股政策面 利好利空"
```
```

- [ ] **Step 4: Remove outdated quality_report section**

Locate lines 203-232 (兜底逻辑部分), delete or update:

```markdown
# DELETE or simplify the 兜底逻辑 section:
# Since we no longer have sectors/zt_pool data collection,
# the tavily_supplement mechanism is no longer needed.
# Just keep the general search requirements.
```

- [ ] **Step 5: Commit template update**

```bash
git add references/daily.md
git commit -m "$(cat <<'EOF'
docs: update daily.md template for Tavily search workflow

- Add Tavily search strategy for chapter 3/4
- Update total search count to 12 (5+5+2)
- Remove outdated 兜底逻辑 (no sector/涨停池 collection)
EOF
)"
```

---

## Task 12: Test Refactored Data Collection

**Files:**
- Test: `run_report.py`

- [ ] **Step 1: Run daily data collection test**

```bash
cd C:/Users/玄阳主/Desktop/fsdownload/a-stock-market-report
python run_report.py 20260423 --mode daily
```

Expected output:
- Only fetch indices + watchlist (no sectors/涨停池)
- JSON output contains indices + watchlist_a + watchlist_hk
- No errors about deleted functions

- [ ] **Step 2: Verify JSON output structure**

Check the generated JSON file:

```bash
cat output/data/data_20260423.json | head -50
```

Expected structure matches spec (Task 6 Step 4).

- [ ] **Step 3: Run weekly data collection test**

```bash
python run_report.py 20260423 --mode weekly
```

Expected: Similar simplified output.

- [ ] **Step 4: Commit test verification**

```bash
git add output/data/*.json
git commit -m "test: verify refactored data collection works"
```

---

## Task 13: Final Cleanup and Verification

- [ ] **Step 1: Search for any remaining deleted function references**

```bash
cd C:/Users/玄阳主/Desktop/fsdownload/a-stock-market-report
grep -r "fetch_multi_sectors\|fetch_multi_zt_pool\|analyze_hot_sectors\|analyze_hot_stocks" --include="*.py" .
```

Expected: No results

- [ ] **Step 2: Verify code line count reduction**

```bash
wc -l scripts/fetchlayer.py scripts/datafoundation.py run_report.py
```

Expected: ~2500 total (down from ~3500)

- [ ] **Step 3: Create final summary commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: complete data collection simplification

Summary:
- Removed ~850 lines of sector/涨停池 collection code
- Simplified validate_data to only check indices + watchlist
- Updated JSON output structure (removed sectors/zt_pool fields)
- Updated daily.md template for Tavily search workflow
- Retained three-layer architecture structure

Files changed:
- scripts/fetchlayer.py: -500 lines
- scripts/datafoundation.py: -200 lines
- run_report.py: -150 lines
- references/daily.md: template updates
EOF
)"
```

---

## Self-Review Checklist

After completing all tasks, verify:

- [ ] `fetch_multi_sectors()` completely removed from fetchlayer.py
- [ ] `fetch_multi_zt_pool()` completely removed from fetchlayer.py
- [ ] `analyze_hot_sectors()` and `analyze_hot_stocks()` removed from both files
- [ ] `TavilySupplementGenerator` removed from datafoundation.py
- [ ] `validate_data()` only checks indices + watchlist
- [ ] JSON output has no sectors/hot_sectors/zt_pool/hot_stocks fields
- [ ] `run_report.py --mode daily` executes without errors
- [ ] No import errors for deleted functions
- [ ] daily.md template updated with Tavily search strategy
- [ ] Code reduced by approximately 850 lines

---

## Verification Commands

Final verification:

```bash
# 1. No deleted function references
grep -r "fetch_multi_sectors\|fetch_multi_zt_pool" --include="*.py" . && echo "FAIL" || echo "PASS"

# 2. Line count check
wc -l scripts/fetchlayer.py scripts/datafoundation.py run_report.py

# 3. Test execution
python run_report.py 20260423 --mode daily

# 4. JSON structure check  
python -c "import json; d=json.load(open('output/data/data_20260423.json')); print('sectors' in d or 'zt_pool' in d and 'FAIL' or 'PASS')"
```

---

**Plan Complete. Ready for execution.**