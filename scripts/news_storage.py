#!/usr/bin/env python3
"""
新闻数据存储与聚合模块

功能:
- save_daily_news: 保存每日 Tavily 搜索结果
- load_daily_news: 加载指定日期的新闻
- aggregate_weekly_news: 聚合周度新闻（复用日报）
- aggregate_monthly_news: 聚合月度新闻（复用日报/周报）
"""

import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional


# ========== 路径配置 ==========
SKILL_ROOT = Path(__file__).parent.parent
CONFIG_PATH = SKILL_ROOT / "config.json"


def load_config() -> dict:
    """加载配置文件"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except UnicodeDecodeError:
        with open(CONFIG_PATH, 'r', encoding='gbk') as f:
            return json.load(f)


def resolve_path(path_str: str, base_dir: Path = SKILL_ROOT) -> Path:
    """解析路径（相对/绝对）"""
    if not path_str:
        return base_dir
    path = Path(path_str)
    return path if path.is_absolute() else base_dir / path_str


_config = load_config()
OUTPUT_DIR = resolve_path(_config.get("paths", {}).get("output_dir", "output"))
NEWS_DIR = resolve_path(_config.get("paths", {}).get("news_dir", "output/news"))


# ========== 日报新闻存储 ==========

def save_daily_news(
    date: str,
    search_queries: List[str],
    results: List[Dict],
    search_depth: str = "basic",
    time_range: str = "day"
) -> str:
    """
    保存每日新闻数据到 output/news/daily/

    Args:
        date: 日期 YYYY-MM-DD 或 YYYYMMDD
        search_queries: 搜索查询列表
        results: Tavily 搜索结果列表（每个元素包含 query 和 items）
        search_depth: 搜索深度
        time_range: 时间范围

    Returns:
        保存的文件路径
    """
    # 标准化日期格式
    if len(date) == 8 and date.isdigit():
        date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    else:
        date_fmt = date

    # 构建新闻数据结构
    news_data = {
        "date": date_fmt,
        "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "search_queries": search_queries,
        "search_depth": search_depth,
        "time_range": time_range,
        "results": results,
        "meta": {
            "total_items": sum(len(r.get("items", [])) for r in results),
            "total_queries": len(search_queries),
            "credit_used": len(search_queries)
        }
    }

    # 确保目录存在
    daily_dir = NEWS_DIR / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    # 保存文件
    filename = f"news_{date_fmt.replace('-', '')}.json"
    filepath = daily_dir / filename

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(news_data, f, ensure_ascii=False, indent=2)

    return str(filepath)


def load_daily_news(date: str) -> Optional[Dict]:
    """
    加载指定日期的新闻数据

    Args:
        date: 日期 YYYY-MM-DD 或 YYYYMMDD

    Returns:
        新闻数据字典，不存在返回 None
    """
    # 标准化日期格式
    if len(date) == 8 and date.isdigit():
        date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    else:
        date_fmt = date

    filename = f"news_{date_fmt.replace('-', '')}.json"
    filepath = NEWS_DIR / "daily" / filename

    if not filepath.exists():
        return None

    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def list_daily_news(date_range: tuple = None) -> List[str]:
    """
    列出 daily news 目录中的文件

    Args:
        date_range: 可选的日期范围 (start_date, end_date)，格式 YYYY-MM-DD

    Returns:
        文件名列表
    """
    daily_dir = NEWS_DIR / "daily"
    if not daily_dir.exists():
        return []

    files = sorted([f.name for f in daily_dir.glob("news_*.json")])

    if date_range:
        start, end = date_range
        start_num = int(start.replace('-', '')[:8])
        end_num = int(end.replace('-', '')[:8])
        files = [
            f for f in files
            if start_num <= int(f.replace('news_', '').replace('.json', '')) <= end_num
        ]

    return files


# ========== 周报新闻聚合 ==========

def get_week_number(date: str) -> tuple:
    """
    获取日期所在的周数和周一/周五日期

    Args:
        date: 日期 YYYY-MM-DD

    Returns:
        (year, week_num, monday, friday)
    """
    dt = datetime.strptime(date, '%Y-%m-%d')
    year, week_num, _ = dt.isocalendar()

    # 计算周一和周五
    monday = dt - timedelta(days=dt.weekday())
    friday = monday + timedelta(days=4)

    return year, week_num, monday.strftime('%Y-%m-%d'), friday.strftime('%Y-%m-%d')


def aggregate_weekly_news(date: str, supplement_results: List[Dict] = None) -> Dict:
    """
    聚合周度新闻（复用日报数据）

    Args:
        date: 周内任意日期 YYYY-MM-DD
        supplement_results: 补充搜索结果（用于覆盖未覆盖的新闻）

    Returns:
        聚合后的新闻数据字典
    """
    year, week_num, monday, friday = get_week_number(date)

    # 读取本周周一至周五的 daily news 文件
    daily_news_list = []
    for d in list_daily_news((monday, friday)):
        news_data = load_daily_news(d.replace('news_', '').replace('.json', ''))
        if news_data:
            daily_news_list.append(news_data)

    # 聚合所有新闻
    all_results = []
    all_queries = []
    seen_urls = set()  # 用于去重

    for news_data in daily_news_list:
        for result in news_data.get("results", []):
            query = result.get("query", "")
            if query not in all_queries:
                all_queries.append(query)

            items = []
            for item in result.get("items", []):
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    items.append(item)

            if items:
                all_results.append({
                    "query": query,
                    "source_date": news_data.get("date"),
                    "items": items
                })

    # 添加补充搜索结果
    if supplement_results:
        for result in supplement_results:
            query = result.get("query", "")
            if query not in all_queries:
                all_queries.append(query)

            items = []
            for item in result.get("items", []):
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    items.append(item)

            if items:
                all_results.append({
                    "query": query,
                    "source": "supplement",
                    "items": items
                })

    # 构建周度新闻数据
    weekly_data = {
        "date": date,
        "week_number": f"{year}-W{week_num:02d}",
        "week_start": monday,
        "week_end": friday,
        "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "source_type": "daily_aggregated",
        "daily_news_count": len(daily_news_list),
        "search_queries": all_queries,
        "results": all_results,
        "meta": {
            "total_items": sum(len(r.get("items", [])) for r in all_results),
            "total_queries": len(all_queries),
            "supplement_queries": len(supplement_results) if supplement_results else 0
        }
    }

    return weekly_data


def save_weekly_news(weekly_data: Dict) -> str:
    """
    保存周度新闻数据

    Args:
        weekly_data: aggregate_weekly_news 返回的数据

    Returns:
        保存的文件路径
    """
    weekly_dir = NEWS_DIR / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)

    week_number = weekly_data.get("week_number", "")
    filename = f"news_{week_number}.json"
    filepath = weekly_dir / filename

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(weekly_data, f, ensure_ascii=False, indent=2)

    return str(filepath)


# ========== 月报新闻聚合 ==========

def aggregate_monthly_news(month: str, supplement_results: List[Dict] = None) -> Dict:
    """
    聚合月度新闻（复用日报/周报数据）

    Args:
        month: 月份 YYYY-MM
        supplement_results: 补充搜索结果

    Returns:
        聚合后的新闻数据字典
    """
    # 计算月初和月末
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 12:
        next_month = f"{year + 1}-01"
    else:
        next_month = f"{year}-{mon + 1:02d}"

    month_start = f"{month}-01"
    # 月末日期需要计算
    from calendar import monthrange
    last_day = monthrange(year, mon)[1]
    month_end = f"{month}-{last_day:02d}"

    # 读取本月所有 daily news 文件
    daily_news_list = []
    for d in list_daily_news((month_start, month_end)):
        news_data = load_daily_news(d.replace('news_', '').replace('.json', ''))
        if news_data:
            daily_news_list.append(news_data)

    # 尝试读取本周所有 weekly news 文件
    weekly_dir = NEWS_DIR / "weekly"
    weekly_news_list = []
    if weekly_dir.exists():
        for f in weekly_dir.glob(f"news_{year}-W*.json"):
            with open(f, 'r', encoding='utf-8') as fp:
                weekly_data = json.load(fp)
                # 检查是否属于本月
                ws = weekly_data.get("week_start", "")
                we = weekly_data.get("week_end", "")
                if ws.startswith(month) or we.startswith(month):
                    weekly_news_list.append(weekly_data)

    # 聚合所有新闻（去重）
    all_results = []
    all_queries = []
    seen_urls = set()

    for news_data in daily_news_list:
        for result in news_data.get("results", []):
            query = result.get("query", "")
            if query not in all_queries:
                all_queries.append(query)

            items = []
            for item in result.get("items", []):
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    items.append(item)

            if items:
                all_results.append({
                    "query": query,
                    "source_type": "daily",
                    "source_date": news_data.get("date"),
                    "items": items
                })

    for weekly_data in weekly_news_list:
        for result in weekly_data.get("results", []):
            query = result.get("query", "")
            if query not in all_queries:
                all_queries.append(query)

            items = []
            for item in result.get("items", []):
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    items.append(item)

            if items:
                all_results.append({
                    "query": query,
                    "source_type": "weekly",
                    "source_week": weekly_data.get("week_number"),
                    "items": items
                })

    # 添加补充搜索结果
    if supplement_results:
        for result in supplement_results:
            query = result.get("query", "")
            if query not in all_queries:
                all_queries.append(query)

            items = []
            for item in result.get("items", []):
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    items.append(item)

            if items:
                all_results.append({
                    "query": query,
                    "source": "supplement",
                    "items": items
                })

    # 构建月度新闻数据
    monthly_data = {
        "month": month,
        "month_start": month_start,
        "month_end": month_end,
        "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "source_type": "daily_weekly_aggregated",
        "daily_news_count": len(daily_news_list),
        "weekly_news_count": len(weekly_news_list),
        "search_queries": all_queries,
        "results": all_results,
        "meta": {
            "total_items": sum(len(r.get("items", [])) for r in all_results),
            "total_queries": len(all_queries),
            "supplement_queries": len(supplement_results) if supplement_results else 0
        }
    }

    return monthly_data


def save_monthly_news(monthly_data: Dict) -> str:
    """
    保存月度新闻数据

    Args:
        monthly_data: aggregate_monthly_news 返回的数据

    Returns:
        保存的文件路径
    """
    monthly_dir = NEWS_DIR / "monthly"
    monthly_dir.mkdir(parents=True, exist_ok=True)

    month = monthly_data.get("month", "")
    filename = f"news_{month}.json"
    filepath = monthly_dir / filename

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(monthly_data, f, ensure_ascii=False, indent=2)

    return str(filepath)


# ========== 辅助函数 ==========

def check_news_coverage(news_data: Dict, required_topics: List[str]) -> List[str]:
    """
    检查新闻覆盖率，返回未覆盖的主题

    Args:
        news_data: 新闻数据
        required_topics: 需要覆盖的主题列表

    Returns:
        未覆盖的主题列表
    """
    covered_topics = []
    for result in news_data.get("results", []):
        query = result.get("query", "")
        for topic in required_topics:
            if topic.lower() in query.lower():
                covered_topics.append(topic)

    uncovered = [t for t in required_topics if t not in covered_topics]
    return uncovered


# ========== CLI 入口 ==========

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="新闻数据管理工具")
    parser.add_argument("action", choices=["list", "load", "aggregate"])
    parser.add_argument("--date", help="日期 YYYY-MM-DD")
    parser.add_argument("--month", help="月份 YYYY-MM")
    parser.add_argument("--type", choices=["daily", "weekly", "monthly"], default="daily")

    args = parser.parse_args()

    if args.action == "list":
        if args.type == "daily":
            files = list_daily_news()
            print(f"日报新闻文件 ({len(files)} 个):")
            for f in files:
                print(f"  {f}")
        else:
            print("暂不支持其他类型列表")

    elif args.action == "load":
        if args.type == "daily" and args.date:
            data = load_daily_news(args.date)
            if data:
                print(json.dumps(data, ensure_ascii=False, indent=2)[:1000])
            else:
                print(f"未找到 {args.date} 的新闻数据")

    elif args.action == "aggregate":
        if args.type == "weekly" and args.date:
            data = aggregate_weekly_news(args.date)
            print(f"聚合周度新闻: {data.get('week_number')}")
            print(f"涵盖日期: {data.get('week_start')} - {data.get('week_end')}")
            print(f"总新闻数: {data.get('meta', {}).get('total_items')}")
        elif args.type == "monthly" and args.month:
            data = aggregate_monthly_news(args.month)
            print(f"聚合月度新闻: {data.get('month')}")
            print(f"总新闻数: {data.get('meta', {}).get('total_items')}")