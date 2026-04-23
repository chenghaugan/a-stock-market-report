#!/usr/bin/env python3
"""
飞书机器人 Webhook 推送脚本

功能:
- 将报告摘要推送到飞书群
- 支持多个 webhook 配置
- 自动提取报告关键信息生成飞书卡片消息

使用:
    python push_to_feishu.py push --file report.md --type daily
"""

import sys
import os
import json
import argparse
import re
from pathlib import Path
from datetime import datetime
import requests

# ========== 配置加载 ==========
CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config(config_path: str = None) -> dict:
    """加载配置文件，自动检测编码"""
    if config_path is None:
        config_path = str(CONFIG_PATH)

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except UnicodeDecodeError:
        with open(config_path, 'r', encoding='gbk') as f:
            data = json.load(f)
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data


# ========== 报告解析 ==========

def extract_report_metadata(content: str) -> dict:
    """
    从报告 Markdown 中提取元数据

    Args:
        content: Markdown 报告内容

    Returns:
        包含 date, title, quality_score 等的字典
    """
    metadata = {}

    # 提取 frontmatter
    frontmatter_match = re.search(r'^---\n(.*?)\n---', content, re.MULTILINE)
    if frontmatter_match:
        fm_text = frontmatter_match.group(1)
        for line in fm_text.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key in ['date', 'title', 'tags', 'generated_at', 'quality_score']:
                    metadata[key] = value

    return metadata


def extract_market_summary(content: str) -> dict:
    """
    提取市场概览章节内容

    Returns:
        包含指数数据的字典
    """
    summary = {
        'indices': [],
        'market_feature': ''
    }

    # 匹配市场全景概览章节
    section_match = re.search(
        r'## 一、市场全景概览\n(.*?)## 二、',
        content,
        re.MULTILINE | re.DOTALL
    )

    if section_match:
        section = section_match.group(1)

        # 提取指数表格
        table_match = re.search(r'\| 指数 \|.*?\n\| --- \|.*?\n(.*?)\n\n', section, re.MULTILINE)
        if table_match:
            table_rows = table_match.group(1)
            for line in table_rows.strip().split('\n'):
                if line.startswith('|'):
                    parts = [p.strip() for p in line.split('|')[1:-1]]
                    if len(parts) >= 4 and parts[0] != '指数':
                        summary['indices'].append({
                            'name': parts[0],
                            'price': parts[1],
                            'change': parts[2],
                            'pct': parts[3]
                        })

        # 提取市场特征描述
        feature_match = re.search(r'\*\*市场特征\*\*[：:]\s*(.*?)\n\n', section, re.MULTILINE | re.DOTALL)
        if feature_match:
            summary['market_feature'] = feature_match.group(1).strip()

    return summary


def extract_top_sectors(content: str, top_n: int = 3) -> list:
    """
    提取热门板块 TOP N

    Returns:
        板块列表，每项包含 name, pct
    """
    sectors = []

    # 匹配热门板块章节
    section_match = re.search(
        r'## 三、.*?热门板块.*?\n(.*?)## 四、',
        content,
        re.MULTILINE | re.DOTALL
    )

    if section_match:
        section = section_match.group(1)

        # 提取板块表格
        table_match = re.search(r'\| 排名 \|.*?\n\| --- \|.*?\n(.*?)\n\n', section, re.MULTILINE)
        if table_match:
            table_rows = table_match.group(1)
            for line in table_rows.strip().split('\n')[:top_n]:
                if line.startswith('|'):
                    parts = [p.strip() for p in line.split('|')[1:-1]]
                    if len(parts) >= 3 and parts[0].isdigit():
                        sectors.append({
                            'rank': parts[0],
                            'name': parts[1],
                            'pct': parts[2]
                        })

    return sectors


def extract_watchlist_summary(content: str) -> dict:
    """
    提取自选股统计

    Returns:
        包含 A股和港股涨跌统计
    """
    watchlist = {
        'a_gainers': 0,
        'a_losers': 0,
        'a_top_gainer': '',
        'hk_gainers': 0,
        'hk_losers': 0
    }

    # 匹配自选股章节
    section_match = re.search(
        r'## 五、自选股跟踪\n(.*?)## 六、',
        content,
        re.MULTILINE | re.DOTALL
    )

    if section_match:
        section = section_match.group(1)

        # 提取自选小结
        summary_match = re.search(r'\*\*自选小结\*\*[：:]\s*(.*?)\n', section)
        if summary_match:
            summary_text = summary_match.group(1)

            # 提取上涨数量
            gainers_match = re.search(r'上涨[:：]\s*(\d+)', summary_text)
            if gainers_match:
                watchlist['a_gainers'] = int(gainers_match.group(1))

            # 提取下跌数量
            losers_match = re.search(r'下跌[:：]\s*(\d+)', summary_text)
            if losers_match:
                watchlist['a_losers'] = int(losers_match.group(1))

        # 提取涨幅超3%表格
        gainers_table = re.search(
            r'### 涨幅异动.*?\n\| 代码 \|.*?\n\| --- \|.*?\n(.*?)\n(?:### |## )',
            section,
            re.MULTILINE | re.DOTALL
        )
        if gainers_table:
            first_row = gainers_table.group(1).strip().split('\n')[0]
            if first_row.startswith('|'):
                parts = [p.strip() for p in first_row.split('|')[1:-1]]
                if len(parts) >= 2:
                    watchlist['a_top_gainer'] = f"{parts[1]} {parts[2]}"

    return watchlist


# ========== 飞书消息构建 ==========

def build_feishu_card(metadata: dict, summary: dict, sectors: list, watchlist: dict) -> dict:
    """
    构建飞书消息卡片

    Args:
        metadata: 报告元数据
        summary: 市场概览
        sectors: 热门板块
        watchlist: 自选股统计

    Returns:
        飞书消息卡片 JSON 结构
    """
    date = metadata.get('date', '未知日期')
    quality_score = metadata.get('quality_score', '未知')
    report_type = '日报'
    if '周报' in metadata.get('title', ''):
        report_type = '周报'
    elif '月报' in metadata.get('title', ''):
        report_type = '月报'

    # 构建指数数据文本
    indices_text = ""
    for idx in summary.get('indices', [])[:4]:
        change_symbol = '+' if idx['change'].startswith('+') else ''
        indices_text += f"• **{idx['name']}**: {idx['price']} ({change_symbol}{idx['pct']})\n"

    # 构建热门板块文本
    sectors_text = ""
    for sec in sectors:
        sectors_text += f"• **{sec['name']}**: {sec['pct']}\n"

    # 构建自选股文本
    watchlist_text = f"• 上涨: {watchlist['a_gainers']}只\n• 下跌: {watchlist['a_losers']}只\n"
    if watchlist['a_top_gainer']:
        watchlist_text += f"• 领涨: {watchlist['a_top_gainer']}\n"

    # 市场特征截取前200字
    market_feature = summary.get('market_feature', '')
    if len(market_feature) > 200:
        market_feature = market_feature[:200] + "..."

    # 构建卡片消息
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"[{date}] A股{report_type}"
                },
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**数据质量**: {quality_score}\n**生成时间**: {metadata.get('generated_at', '未知')}"
                    }
                },
                {
                    "tag": "hr"
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**市场指数**\n{indices_text}"
                    }
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**热门板块 TOP3**\n{sectors_text}"
                    }
                },
                {
                    "tag": "hr"
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**自选股统计**\n{watchlist_text}"
                    }
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "数据来源: 腾讯/新浪/东财 API + Tavily 财经新闻"
                        }
                    ]
                }
            ]
        }
    }

    # 如果有市场特征，添加一个元素
    if market_feature:
        card['card']['elements'].insert(3, {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**市场特征**\n{market_feature}"
            }
        })

    return card


def build_simple_text_message(metadata: dict, summary: dict) -> dict:
    """
    构建简单文本消息（备用格式）

    Args:
        metadata: 报告元数据
        summary: 市场概览

    Returns:
        飞书文本消息 JSON 结构
    """
    date = metadata.get('date', '未知日期')
    indices = summary.get('indices', [])

    indices_line = " | ".join([
        f"{idx['name']}: {idx['pct']}"
        for idx in indices[:4]
    ])

    text = f"""[{date}] A股日报

指数: {indices_line}

数据已推送到 Obsidian，点击查看完整报告。

生成时间: {metadata.get('generated_at', '未知')}
"""

    return {
        "msg_type": "text",
        "content": {
            "text": text
        }
    }


# ========== 飞书推送 ==========

def push_to_feishu(
    message: dict,
    webhook_url: str,
    timeout: int = 30
) -> dict:
    """
    推送消息到飞书 Webhook

    Args:
        message: 飞书消息结构
        webhook_url: Webhook URL
        timeout: 超时时间（秒）

    Returns:
        {"success": bool, "error": str, "status": int}
    """
    try:
        response = requests.post(
            webhook_url,
            json=message,
            headers={"Content-Type": "application/json"},
            timeout=timeout
        )

        if response.status_code == 200:
            result = response.json()
            if result.get('code') == 0 or result.get('StatusCode') == 0:
                return {
                    "success": True,
                    "status": response.status_code,
                    "webhook": webhook_url
                }
            else:
                return {
                    "success": False,
                    "error": result.get('msg', 'Unknown error'),
                    "status": response.status_code,
                    "webhook": webhook_url
                }
        else:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:100]}",
                "status": response.status_code,
                "webhook": webhook_url
            }
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "Timeout",
            "webhook": webhook_url
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "webhook": webhook_url
        }


def push_to_all_feishu(
    content: str,
    config: dict,
    use_card: bool = True
) -> list:
    """
    推送到所有配置的飞书 Webhook

    Args:
        content: Markdown 报告内容
        config: 配置字典
        use_card: 是否使用卡片消息（True）或简单文本（False）

    Returns:
        推送结果列表
    """
    results = []

    # 提取报告信息
    metadata = extract_report_metadata(content)
    summary = extract_market_summary(content)
    sectors = extract_top_sectors(content)
    watchlist = extract_watchlist_summary(content)

    # 构建消息
    if use_card:
        message = build_feishu_card(metadata, summary, sectors, watchlist)
    else:
        message = build_simple_text_message(metadata, summary)

    # 获取 webhook 配置
    feishu_config = config.get('feishu', {})

    # 支持多个 webhook（webhooks 列表）
    webhooks = feishu_config.get('webhooks', [])
    if not webhooks:
        # 单个 webhook（webhook 字段）
        single_webhook = feishu_config.get('webhook', '')
        if single_webhook:
            webhooks = [single_webhook]

    # 推送到每个 webhook
    for webhook_url in webhooks:
        if not webhook_url:
            continue

        result = push_to_feishu(message, webhook_url)
        results.append(result)

    return results


# ========== CLI 入口 ==========

def main():
    parser = argparse.ArgumentParser(description="飞书机器人推送工具")
    parser.add_argument("action", choices=["push", "test"], help="操作类型")
    parser.add_argument("--file", "-f", help="报告文件路径")
    parser.add_argument("--type", choices=["daily", "weekly", "monthly"], default="daily", help="报告类型")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("--simple", action="store_true", help="使用简单文本格式而非卡片")

    args = parser.parse_args()

    config = load_config(args.config)

    if args.action == "test":
        # 测试连接
        webhooks = config.get('feishu', {}).get('webhooks', [])
        if not webhooks:
            single = config.get('feishu', {}).get('webhook', '')
            if single:
                webhooks = [single]

        if not webhooks:
            print("[FAIL] 未配置飞书 Webhook")
            sys.exit(1)

        for webhook in webhooks:
            test_msg = {
                "msg_type": "text",
                "content": {
                    "text": "[OK] 飞书推送测试成功"
                }
            }
            result = push_to_feishu(test_msg, webhook)
            status = "[OK]" if result['success'] else "[FAIL]"
            print(f"{status} Webhook: {webhook[:50]}... - {result.get('error', 'OK')}")

        return

    if args.action == "push":
        if not args.file:
            print("[FAIL] 需要指定 --file 参数")
            sys.exit(1)

        # 读取报告内容
        with open(args.file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 推送
        results = push_to_all_feishu(content, config, use_card=not args.simple)

        success_count = sum(1 for r in results if r['success'])
        print(f"推送结果: {success_count}/{len(results)} 成功")

        for r in results:
            status = "[OK]" if r['success'] else "[FAIL]"
            webhook_short = r['webhook'][:40] + "..." if len(r['webhook']) > 40 else r['webhook']
            print(f"  {status} {webhook_short}")
            if not r['success']:
                print(f"     错误: {r.get('error', 'Unknown')}")

        if success_count < len(results):
            sys.exit(1)


if __name__ == "__main__":
    main()