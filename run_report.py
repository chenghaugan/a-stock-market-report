#!/opt/data/home/venv/hermes/bin/python3
"""
A股市场数据采集 - 多源交叉验证版（v9）

架构:
- scripts/datafoundation.py: 底层（curl封装、编码检测、质量评分基类）
- scripts/fetchlayer.py: 中层（多源获取 + 交叉验证）
- run_report.py: 顶层（流程编排 + validate_data + 输出）

数据源: 腾讯/新浪/东方财富 (curl 直连)，三源并发 + 交叉验证
输出: 采集的市场数据(JSON) + 本地文件 + quality_report
"""
import json
import datetime
import sys
import os
import re
import warnings
import decimal
from pathlib import Path
from datetime import date, timedelta
from typing import Dict, List, Any, Tuple

warnings.filterwarnings("ignore")

# ========== 导入多源采集层 ==========
SCRIPTS_DIR = Path(__file__).parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from fetchlayer import (
        fetch_multi_index,
        fetch_multi_sectors,
        fetch_multi_zt_pool,
        fetch_multi_watchlist_a,
        fetch_multi_watchlist_hk,
    )
    from datafoundation import SourceQuality
    FETCHLAYER_READY = True
except ImportError as e:
    print(f"⚠️ fetchlayer 导入失败: {e}", file=sys.stderr)
    FETCHLAYER_READY = False

# ========== 配置 ==========
CONFIG_PATH = Path(__file__).parent / "config.json"
SKILL_ROOT = Path(__file__).parent  # skill根目录

def load_config():
    """加载配置文件，自动检测编码（UTF-8优先，GBK兜底）"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except UnicodeDecodeError:
        with open(CONFIG_PATH, 'r', encoding='gbk') as f:
            return json.load(f)

def resolve_path(path_str: str, base_dir: Path = SKILL_ROOT) -> Path:
    """
    解析路径，支持相对路径和绝对路径

    Args:
        path_str: 配置中的路径字符串
        base_dir: 相对路径的基准目录（默认为skill根目录）

    Returns:
        解析后的绝对路径
    """
    if not path_str:
        return base_dir

    path = Path(path_str)
    if path.is_absolute():
        return path
    else:
        return base_dir / path_str

_config = load_config()
OUTPUT_DIR = resolve_path(_config.get("paths", {}).get("output_dir", "output"))
DATA_DIR = resolve_path(_config.get("paths", {}).get("data_dir", "output/data"))
NEWS_DIR = resolve_path(_config.get("paths", {}).get("news_dir", "output/news"))
REPORTS_DIR = resolve_path(_config.get("paths", {}).get("reports_dir", "output/reports"))
SKILL_DIR = resolve_path(_config.get("paths", {}).get("skill_dir", ""), SKILL_ROOT)

# ========== 交易日历工具 ==========
AKSHARE_READY = False
try:
    import akshare as ak
    AKSHARE_READY = True
except ImportError:
    pass

def get_trade_calendar() -> List[str]:
    """获取交易日历（历史所有交易日），返回 YYYY-MM-DD 格式"""
    if not AKSHARE_READY:
        return []
    try:
        import akshare as ak
        trade_cal = ak.tool_trade_date_hist_sina()
        result = []
        for d in trade_cal:
            if hasattr(d, 'strftime'):
                result.append(d.strftime('%Y-%m-%d'))
            elif isinstance(d, str) and '-' in d:
                result.append(d[:10])
        return result
    except Exception as e:
        print(f"  获取交易日历失败: {e}", file=sys.stderr)
        return []

def get_last_trading_date(ref_date: str = None) -> str:
    """获取距离 ref_date 最近的交易日（不含 ref_date 本身）"""
    if ref_date is None:
        ref_date = date.today().strftime('%Y-%m-%d')
    
    trade_dates = get_trade_calendar()
    if not trade_dates:
        # 无法获取交易日历，使用简单的周末判断
        ref_dt = datetime.datetime.strptime(ref_date, '%Y-%m-%d').date()
        for i in range(1, 10):
            check = ref_dt - timedelta(days=i)
            if check.weekday() < 5:
                return check.strftime('%Y-%m-%d')
        return ref_date
    
    ref_normalized = ref_date.replace('-', '')
    valid_dates = [d for d in trade_dates if d.replace('-', '') <= ref_normalized]
    return valid_dates[-1] if valid_dates else ref_date

def is_trading_day(check_date: str = None) -> bool:
    """检查指定日期是否为交易日"""
    if check_date is None:
        check_date = date.today().strftime('%Y-%m-%d')
    
    trade_dates = get_trade_calendar()
    if not trade_dates:
        check_dt = datetime.datetime.strptime(check_date, '%Y-%m-%d').date()
        return check_dt.weekday() < 5
    return check_date in trade_dates


# ========== 数据质量校验 ==========
def validate_data(sectors: list = None, zt_pool: list = None, indices: list = None, 
                  quality_info: dict = None) -> dict:
    """
    数据质量校验，返回结构化的 quality_report。
    
    评分规则:
    - score >= 0.7 → passed=True, 无需补充
    - 0.5 <= score < 0.7 → passed=True, 警告
    - score < 0.5 → passed=False, 触发 Tavily 补充
    """
    if sectors is None: sectors = []
    if zt_pool is None: zt_pool = []
    if indices is None: indices = []
    
    issues = []
    warnings = []
    source_scores = {}
    tavily_queries = []
    tavily_fields = []
    
    # === 数据完整性检查 ===
    if not sectors:
        issues.append("板块数据为空")
        tavily_queries.append(f"A股行业板块涨幅排名 今日")
        tavily_fields.append("sectors")
        source_scores['sectors'] = 0.0
    elif len(sectors) < 10:
        warnings.append(f"板块数据偏少（仅 {len(sectors)} 个）")
        source_scores['sectors'] = len(sectors) / 50
    else:
        source_scores['sectors'] = 1.0
    
    if not zt_pool:
        warnings.append("涨停池数据为空")
        tavily_queries.append("今日涨停板 涨停原因")
        tavily_fields.append("zt_pool")
        source_scores['zt_pool'] = 0.0
    elif len(zt_pool) < 5:
        warnings.append(f"涨停池数据偏少（仅 {len(zt_pool)} 只）")
        source_scores['zt_pool'] = len(zt_pool) / 50
    else:
        source_scores['zt_pool'] = 1.0
    
    if not indices:
        issues.append("指数数据获取失败")
        tavily_queries.append("上证指数 深证成指 创业板 涨跌幅")
        tavily_fields.append("indices")
        source_scores['indices'] = 0.0
    else:
        source_scores['indices'] = 1.0
    
    # === 异常值检查 ===
    for s in sectors:
        raw_change = s.get("change", 0)
        if isinstance(raw_change, (int, float)):
            if abs(raw_change) > 100:
                issues.append(f"板块 {s.get('name', '?')} 涨跌幅异常: {raw_change:.2f}%")
            elif abs(raw_change) > 50:
                warnings.append(f"板块 {s.get('name', '?')} 涨跌幅偏高: {raw_change:.2f}%")
    
    for z in zt_pool:
        change = z.get("change", 0)
        if isinstance(change, (int, float)) and abs(change) > 15:
            warnings.append(f"涨停股 {z.get('name', '?')} 涨跌幅异常: {change}%")
    
    # === 指数一致性检查 ===
    if len(indices) >= 3:
        pcts = [i.get('change_pct', 0) for i in indices]
        if pcts:
            diff = max(pcts) - min(pcts)
            if diff > 2.0:
                warnings.append(f"三大指数涨跌幅差异过大: {diff:.2f}%")
    
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
        'tavily_supplement': {
            'needed': not passed or len(tavily_queries) > 0,
            'queries': tavily_queries,
            'fields': tavily_fields,
        },
    }


# ========== 热门排序 ==========
def analyze_hot_sectors(sectors: List[Dict], top_n: int = 5) -> List[Dict]:
    """热门板块排序（涨幅 TOP N）"""
    sorted_sectors = sorted(
        [s for s in sectors if s.get('change', 0) != 0],
        key=lambda x: abs(x.get('change', 0)),
        reverse=True
    )
    return sorted_sectors[:top_n]

def analyze_hot_stocks(zt_pool: List[Dict], top_n: int = 5) -> List[Dict]:
    """热门个股排序（涨停次数/连板 TOP N）"""
    sorted_stocks = sorted(
        zt_pool,
        key=lambda x: x.get('zt_times', 0) or x.get('change', 0),
        reverse=True
    )
    return sorted_stocks[:top_n]


# ========== 主流程 ==========
def main():
    """支持三种模式：daily / weekly / monthly"""
    if len(sys.argv) < 2:
        print("用法: run_report.py <日期> [--mode daily|Weekly|monthly]", file=sys.stderr)
        sys.exit(1)
    
    date_str = sys.argv[1]
    mode = "daily"
    
    for i, arg in enumerate(sys.argv):
        if arg == "--mode" and i + 1 < len(sys.argv):
            mode = sys.argv[i + 1]
    
    if mode not in ("daily", "weekly", "monthly"):
        print(f"错误: mode 必须为 daily/weekly/monthly", file=sys.stderr)
        sys.exit(1)
    
    if re.match(r'^\d{8}$', date_str):
        date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    
    print(f"=== A股数据采集 (v9 多源交叉验证) ===", file=sys.stderr)
    print(f"请求日期: {date_str}, 模式: {mode}", file=sys.stderr)
    print(f"fetchlayer: {'✅' if FETCHLAYER_READY else '❌'}", file=sys.stderr)
    
    config = load_config()
    
    # === 交易日判断 ===
    today_str = date.today().strftime('%Y-%m-%d')
    input_is_today = (date_str == today_str)
    
    if input_is_today:
        is_today_trading = is_trading_day(date_str)
        target_date = date_str if is_today_trading else get_last_trading_date(date_str)
        data_desc = "今日实时" if is_today_trading else f"最近交易日({target_date})"
    else:
        is_today_trading = False
        target_date = date_str if is_trading_day(date_str) else get_last_trading_date(date_str)
        data_desc = f"{target_date}"
    
    print(f"目标日期: {target_date}, 数据来源: {data_desc}", file=sys.stderr)
    
    # === 根据模式采集 ===
    if mode == "daily":
        output = fetch_daily_data(date_str, target_date, is_today_trading, config)
    elif mode == "weekly":
        output = fetch_weekly_data(date_str, target_date, config)
    elif mode == "monthly":
        output = fetch_monthly_data(date_str, target_date, config)
    
    # 输出 JSON
    print("\n=== JSON_OUTPUT_START ===")
    print(json.dumps(output, ensure_ascii=False))
    print("=== JSON_OUTPUT_END ===")
    
    return 0


def fetch_daily_data(date_str: str, target_date: str, is_today_trading: bool,
                     config: Dict) -> Dict:
    """日报模式：使用多源交叉验证获取数据"""

    if not FETCHLAYER_READY:
        print("❌ fetchlayer 未就绪，无法采集数据", file=sys.stderr)
        return {"error": "fetchlayer_not_ready", "date": date_str}

    # 确保目录存在
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "daily").mkdir(parents=True, exist_ok=True)

    output_path = DATA_DIR / f"data_{target_date.replace('-', '')}.json"
    
    print("\n正在多源采集数据...", file=sys.stderr)
    
    # === 多源并发获取 + 交叉验证 ===
    indices, index_qualities = fetch_multi_index()
    print(f"  指数: {len(indices)} 个, 质量: {[round(i.get('quality_score',0),2) for i in indices]}", file=sys.stderr)
    
    sectors, sector_qualities = fetch_multi_sectors()
    print(f"  板块: {len(sectors)} 个", file=sys.stderr)
    
    zt_pool, zt_qualities = fetch_multi_zt_pool()
    print(f"  涨停: {len(zt_pool)} 只", file=sys.stderr)
    
    a_codes = config.get('watchlist', {}).get('a_shares', [])
    a_stocks, a_qualities = fetch_multi_watchlist_a(a_codes)
    print(f"  A股自选: {len(a_stocks)} 只", file=sys.stderr)
    
    hk_codes = config.get('watchlist', {}).get('hk_stocks', [])
    hk_stocks, hk_qualities = fetch_multi_watchlist_hk(hk_codes)
    print(f"  港股自选: {len(hk_stocks)} 只", file=sys.stderr)
    
    # === 数据质量校验 ===
    quality_report = validate_data(sectors, zt_pool, indices)
    
    all_warnings = quality_report.get('issues', []) + quality_report.get('warnings', [])
    if all_warnings:
        print("\n⚠️ 数据质量警告:", file=sys.stderr)
        for w in all_warnings[:5]:
            print(f"  - {w}", file=sys.stderr)
    
    if not quality_report.get('passed'):
        print(f"⚠️ 数据质量不通过 (score={quality_report.get('quality_score', 0):.2f})", file=sys.stderr)
        print(f"  Tavily补充查询: {quality_report['tavily_supplement']['queries']}", file=sys.stderr)
    
    # === 热门排序 ===
    hot_sectors = analyze_hot_sectors(sectors)
    hot_stocks = analyze_hot_stocks(zt_pool)
    
    # === 自选股分段（修正统计逻辑）===
    # 涨幅>3% 为显著上涨，跌幅>3% 为显著下跌
    # 上涨：pct > 0，下跌：pct < 0，平盘：pct == 0 或接近0
    a_gainers = sorted([s for s in a_stocks if (s.get('pct') or 0) > 0],
                       key=lambda x: x.get('pct', 0), reverse=True)
    a_losers = sorted([s for s in a_stocks if (s.get('pct') or 0) < 0],
                      key=lambda x: x.get('pct', 0))
    a_flat = [s for s in a_stocks if abs(s.get('pct', 0)) < 0.01]

    # 涨幅超3%的个股（异动统计）
    a_gainers_3pct = sorted([s for s in a_stocks if (s.get('pct') or 0) >= 3],
                            key=lambda x: x.get('pct', 0), reverse=True)
    a_losers_3pct = sorted([s for s in a_stocks if (s.get('pct') or 0) <= -3],
                           key=lambda x: x.get('pct', 0))

    hk_gainers = sorted([s for s in hk_stocks if (s.get('pct') or 0) > 0],
                        key=lambda x: x.get('pct', 0), reverse=True)
    hk_losers = sorted([s for s in hk_stocks if (s.get('pct') or 0) < 0],
                       key=lambda x: x.get('pct', 0))
    hk_flat = [s for s in hk_stocks if abs(s.get('pct', 0)) < 0.01]

    hk_gainers_3pct = sorted([s for s in hk_stocks if (s.get('pct') or 0) >= 3],
                            key=lambda x: x.get('pct', 0), reverse=True)
    hk_losers_3pct = sorted([s for s in hk_stocks if (s.get('pct') or 0) <= -3],
                           key=lambda x: x.get('pct', 0))

    # === 构建输出 ===
    output = {
        "date": date_str,
        "target_date": target_date,
        "mode": "daily",
        "is_today_trading": is_today_trading,
        "generated_at": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "akshare_ready": AKSHARE_READY,
        "quality_report": quality_report,
        "indices": indices,
        "sectors": sectors,
        "hot_sectors": hot_sectors,
        "zt_pool": zt_pool,
        "hot_stocks": hot_stocks,
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
            "sectors": "ok" if sectors else "failed",
            "zt_pool": "ok" if zt_pool else "failed",
            "watchlist_a": "ok" if a_stocks else "failed",
            "watchlist_hk": "ok" if hk_stocks else "failed",
        },
    }
    
    # === 本地保存 ===
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n数据已保存: {output_path}", file=sys.stderr)
    
    return output


def fetch_weekly_data(date_str: str, target_date: str, config: Dict) -> Dict:
    """周报模式：使用多源数据"""
    # 确保目录存在
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "weekly").mkdir(parents=True, exist_ok=True)

    output_path = DATA_DIR / f"weekly_{target_date.replace('-', '')}.json"
    
    if output_path.exists():
        print(f"⏭️ 已有周报数据: {output_path}", file=sys.stderr)
        with open(output_path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    if not FETCHLAYER_READY:
        return {"error": "fetchlayer_not_ready", "date": date_str, "mode": "weekly"}
    
    print("\n正在采集周报数据...", file=sys.stderr)
    
    # 使用多源获取
    indices, _ = fetch_multi_index()
    sectors, _ = fetch_multi_sectors()
    zt_pool, _ = fetch_multi_zt_pool()
    
    a_codes = config.get('watchlist', {}).get('a_shares', [])
    a_stocks, _ = fetch_multi_watchlist_a(a_codes)
    
    quality_report = validate_data(sectors, zt_pool, indices)
    hot_sectors = analyze_hot_sectors(sectors)
    hot_stocks = analyze_hot_stocks(zt_pool)
    
    output = {
        "date": date_str,
        "target_date": target_date,
        "mode": "weekly",
        "generated_at": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "quality_report": quality_report,
        "indices": indices,
        "sectors": sectors,
        "hot_sectors": hot_sectors,
        "zt_pool": zt_pool,
        "hot_stocks": hot_stocks,
        "watchlist_a": {"all": a_stocks},
    }
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"周报数据已保存: {output_path}", file=sys.stderr)
    
    return output


def fetch_monthly_data(date_str: str, target_date: str, config: Dict) -> Dict:
    """月报模式：使用多源数据"""
    # 确保目录存在
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "monthly").mkdir(parents=True, exist_ok=True)

    output_path = DATA_DIR / f"monthly_{target_date.replace('-', '')}.json"
    
    if output_path.exists():
        print(f"⏭️ 已有月报数据: {output_path}", file=sys.stderr)
        with open(output_path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    if not FETCHLAYER_READY:
        return {"error": "fetchlayer_not_ready", "date": date_str, "mode": "monthly"}
    
    print("\n正在采集月报数据...", file=sys.stderr)
    
    # 使用多源获取
    indices, _ = fetch_multi_index()
    sectors, _ = fetch_multi_sectors()
    zt_pool, _ = fetch_multi_zt_pool()
    
    a_codes = config.get('watchlist', {}).get('a_shares', [])
    a_stocks, _ = fetch_multi_watchlist_a(a_codes)
    
    quality_report = validate_data(sectors, zt_pool, indices)
    hot_sectors = analyze_hot_sectors(sectors)
    hot_stocks = analyze_hot_stocks(zt_pool)
    
    output = {
        "date": date_str,
        "target_date": target_date,
        "mode": "monthly",
        "generated_at": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "quality_report": quality_report,
        "indices": indices,
        "sectors": sectors,
        "hot_sectors": hot_sectors,
        "zt_pool": zt_pool,
        "hot_stocks": hot_stocks,
        "watchlist_a": {"all": a_stocks},
    }
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"月报数据已保存: {output_path}", file=sys.stderr)
    
    return output


if __name__ == "__main__":
    main()