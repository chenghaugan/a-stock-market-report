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

def parse_markdown_tables(content: str) -> list:
    """
    从 Markdown 中提取所有表格，转换为飞书 table 组件格式

    Args:
        content: Markdown 内容

    Returns:
        飞书 table 组件列表，包含表格内容和位置信息
    """
    tables = []

    # 匹配表格及其前面的章节标题
    # 格式：## 章节名 ... 表格
    table_pattern = r'(## .+?\n.*?)?\n?\| (.*) \|\n\| [-:| ]+\|\n((?:\| .*\|\n)+)'

    def parse_table(match):
        section_title = match.group(1) or ''
        header = match.group(2)
        rows_text = match.group(3)

        # 提取章节名
        section_name = ''
        if section_title:
            section_match = re.search(r'## (.+)', section_title)
            if section_match:
                section_name = section_match.group(1).strip()

        # 解析表头
        headers = [h.strip() for h in header.split('|')]

        # 生成英文列标识符（用于 name 字段）
        column_names = []
        for i, h in enumerate(headers):
            # 使用 col_0, col_1, col_2... 作为标识符
            column_names.append(f"col_{i}")

        # 构建 columns 结构
        columns = []
        for i, h in enumerate(headers):
            col_name = column_names[i]
            # 根据列名判断数据类型
            if h in ['涨跌幅', '涨跌', '周涨跌幅', '月涨跌幅', '涨幅', '跌幅', '涨跌幅%']:
                data_type = "lark_md"  # 使用 lark_md 支持颜色
            else:
                data_type = "text"

            columns.append({
                "name": col_name,
                "display_name": h,  # 显示名用中文
                "data_type": data_type,
                "width": "auto"
            })

        # 解析数据行
        rows = []
        for line in rows_text.strip().split('\n'):
            if line.startswith('|'):
                cells = [c.strip() for c in line.split('|')[1:-1]]
                row = {}
                for i, cell in enumerate(cells):
                    col_name = column_names[i]
                    # 中国股市习惯：红涨绿跌
                    if headers[i] in ['涨跌幅', '涨跌', '周涨跌幅', '月涨跌幅', '涨幅', '跌幅', '涨跌幅%']:
                        if cell.startswith('-') or cell.startswith('↓'):
                            row[col_name] = f"<font color='green'>{cell}</font>"  # 下跌用绿色
                        elif cell.startswith('+') or cell.startswith('↑') or cell == '涨停' or cell == '大涨':
                            row[col_name] = f"<font color='red'>{cell}</font>"  # 上涨用红色
                        else:
                            row[col_name] = cell
                    else:
                        row[col_name] = cell
                rows.append(row)

        return {
            "tag": "table",
            "page_size": min(len(rows), 10),
            "row_height": "low",
            "header_style": {
                "text_align": "left",
                "text_size": "normal",
                "background_style": "grey",
                "text_color": "grey",
                "bold": True
            },
            "columns": columns,
            "rows": rows,
            "_section": section_name  # 内部标记，用于排序
        }

    # 查找所有表格
    for match in re.finditer(table_pattern, content):
        table = parse_table(match)
        tables.append(table)

    return tables


def convert_content_without_tables(content: str) -> str:
    """
    将 Markdown 内容转换为飞书格式，跳过表格部分

    Args:
        content: Markdown 内容

    Returns:
        转换后的内容（不含表格）
    """
    result = content

    # 去掉表格
    table_pattern = r'\| (.*) \|\n\| [-:| ]+\|\n((?:\| .*\|\n)+)'
    result = re.sub(table_pattern, '', result)

    # 转换标题（二级和三级标题）
    result = re.sub(r'^### (.+)$', r'◆ **\1**', result, flags=re.MULTILINE)
    result = re.sub(r'^## (.+)$', r'\n**【\1】**\n', result, flags=re.MULTILINE)
    # 一级标题：去掉 #，只保留文本（用于章节内的小标题，不显示 ===）
    result = re.sub(r'^# (.+)$', r'\n**\1**\n', result, flags=re.MULTILINE)

    # 转换引用行
    result = re.sub(r'^> (.+)$', r'▸ \1', result, flags=re.MULTILINE)

    # 转换分割线
    result = re.sub(r'^---+$', '────────────', result, flags=re.MULTILINE)

    # 转换列表项
    result = re.sub(r'^- (.+)$', r'• \1', result, flags=re.MULTILINE)
    result = re.sub(r'^\* (.+)$', r'• \1', result, flags=re.MULTILINE)
    result = re.sub(r'^(\d+)\. (.+)$', lambda m: f'• {m.group(2)}', result, flags=re.MULTILINE)

    # 处理涨跌数值颜色 - 中国股市习惯：红涨绿跌
    def colorize_change(match):
        value = match.group(0)
        if value.startswith('-') or value.startswith('↓'):
            return f"<font color='green'>{value}</font>"  # 下跌用绿色
        elif value.startswith('+') or value.startswith('↑'):
            return f"<font color='red'>{value}</font>"  # 上涨用红色
        return value

    result = re.sub(r'[+-]\d+\.?\d*%', colorize_change, result)
    result = re.sub(r'↑\d+\.?\d*%', colorize_change, result)
    result = re.sub(r'↓\d+\.?\d*%', colorize_change, result)

    # 去掉多余空行
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result.strip()


def convert_markdown_to_feishu(content: str) -> str:
    """
    将 Markdown 转换为飞书 lark_md 格式

    支持的格式：
    - **粗体** (lark_md原生支持)
    - <font color='red/green/grey'>彩色文本</font>
    - [链接](url) (lark_md原生支持)

    Args:
        content: Markdown 内容

    Returns:
        转换后的内容
    """
    result = content

    # 去掉 frontmatter
    frontmatter_match = re.search(r'^---\n.*?\n---\n', result, re.MULTILINE)
    if frontmatter_match:
        result = result[frontmatter_match.end():]

    # 转换表格（使用新的表格转换函数）
    result = convert_table_to_feishu(result)

    # 转换标题：## 标题 -> 【标题】（保持粗体效果）
    result = re.sub(r'^### (.+)$', r'◆ **\1**', result, flags=re.MULTILINE)
    result = re.sub(r'^## (.+)$', r'\n**【\1】**\n', result, flags=re.MULTILINE)
    result = re.sub(r'^# (.+)$', r'\n**\1**\n', result, flags=re.MULTILINE)

    # 粗体：**文本** 保持不变（lark_md已支持）
    # 注意：不要二次转换，保持原样

    # 转换引用行：> 文本 -> ▸ 文本
    result = re.sub(r'^> (.+)$', r'▸ \1', result, flags=re.MULTILINE)

    # 转换分割线：--- -> ────────────
    result = re.sub(r'^---+$', '────────────', result, flags=re.MULTILINE)

    # 转换列表项：- 文本 -> • 文本
    result = re.sub(r'^- (.+)$', r'• \1', result, flags=re.MULTILINE)
    result = re.sub(r'^\* (.+)$', r'• \1', result, flags=re.MULTILINE)

    # 转换数字列表：1. 文本 -> ① 文本
    result = re.sub(r'^(\d+)\. (.+)$', lambda m: f'• {m.group(2)}', result, flags=re.MULTILINE)

    # 处理涨跌数值，添加颜色 - 中国股市习惯：红涨绿跌
    # 匹配形如 +2.5% 或 -1.3% 的涨跌幅
    def colorize_change(match):
        value = match.group(0)
        if value.startswith('-') or value.startswith('↓'):
            return f"<font color='green'>{value}</font>"  # 下跌用绿色
        elif value.startswith('+') or value.startswith('↑'):
            return f"<font color='red'>{value}</font>"  # 上涨用红色
        return value

    # 在行内文本中查找涨跌幅数值并添加颜色
    result = re.sub(r'[+-]\d+\.?\d*%', colorize_change, result)
    result = re.sub(r'↑\d+\.?\d*%', colorize_change, result)
    result = re.sub(r'↓\d+\.?\d*%', colorize_change, result)

    # 去掉多余空行（超过2个连续空行变成2个）
    result = re.sub(r'\n{3,}', '\n\n', result)

    # 去掉表格分隔行残留
    result = re.sub(r'\n\| [-:| ]+\|\n', '', result)

    return result.strip()


def build_full_report_message(content: str, metadata: dict) -> dict:
    """
    构建完整报告消息（使用飞书 interactive 卡片，包含真正的表格组件）

    按原始报告顺序排列：章节标题 → 表格 → 章节内容

    Args:
        content: 完整 Markdown 报告内容
        metadata: 报告元数据

    Returns:
        飞书 interactive 消息 JSON 结构
    """
    # 从metadata获取日期，如果没有则从content提取
    date = metadata.get('date', '')
    if not date:
        # 从frontmatter提取日期
        fm_date_match = re.search(r'^date:\s*(\d{4}-\d{2}-\d{2})', content, re.MULTILINE)
        if fm_date_match:
            date = fm_date_match.group(1)

    report_type = '日报'
    if '周报' in metadata.get('title', '') or '周报' in content:
        report_type = '周报'
    elif '月报' in metadata.get('title', '') or '月报' in content:
        report_type = '月报'

    # 去掉 frontmatter - 完整移除，不显示
    frontmatter_match = re.search(r'^---\n.*?\n---\n', content, re.MULTILINE)
    if frontmatter_match:
        content = content[frontmatter_match.end():]

    # 去掉生成时间和数据来源行
    content = re.sub(r'^生成时间:.*\n', '', content, flags=re.MULTILINE)
    content = re.sub(r'^> 数据来源:.*\n', '', content, flags=re.MULTILINE)

    # 构建卡片元素列表 - 按原始顺序排列
    elements = []

    # 添加报告标题
    title_match = re.search(r'^# (.+)', content)
    report_title = ''
    if title_match:
        report_title = title_match.group(1).strip()
        # 使用大标题样式
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**{report_title}**"
            }
        })
        elements.append({"tag": "hr"})
        # 去掉原标题
        content = content[title_match.end():]

    # 按章节分割内容
    sections = re.split(r'\n(?=## )', content)

    table_count = 0  # 飞书限制每卡片最多5个表格
    max_length = 8000

    for section in sections:
        if not section.strip():
            continue

        # 解析章节标题
        section_title_match = re.search(r'^## (.+)', section)
        section_title = ''
        if section_title_match:
            section_title = section_title_match.group(1).strip()
            # 添加章节标题元素 - 使用醒目格式
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**【{section_title}】**"
                }
            })

        # 按原始顺序处理章节内容（表格和文本交替）
        # 使用更精确的方式：找到所有表格位置，然后按顺序排列

        table_pattern = r'\n?\| (.*) \|\n\| [-:| ]+\|\n((?:\| .*\|\n)+)'

        # 找到所有表格的位置
        table_matches = list(re.finditer(table_pattern, section))

        # 按位置排序，构建内容块列表
        content_blocks = []
        last_end = 0

        # 先添加章节标题后的内容（如果有）
        if section_title_match:
            last_end = section_title_match.end()

        for match in table_matches:
            # 添加表格前的文本块
            text_before = section[last_end:match.start()].strip()
            if text_before:
                content_blocks.append(('text', text_before))

            # 解析表格
            header = match.group(1)
            rows_text = match.group(2)

            headers = [h.strip() for h in header.split('|')]
            column_names = [f"col_{i}" for i in range(len(headers))]

            columns = []
            for i, h in enumerate(headers):
                if h in ['涨跌幅', '涨跌', '周涨跌幅', '月涨跌幅', '涨幅', '跌幅', '涨跌幅%', '最新价', '涨跌']:
                    data_type = "lark_md"
                else:
                    data_type = "text"
                columns.append({
                    "name": column_names[i],
                    "display_name": h,
                    "data_type": data_type,
                    "width": "auto"
                })

            rows = []
            for line in rows_text.strip().split('\n'):
                if line.startswith('|'):
                    cells = [c.strip() for c in line.split('|')[1:-1]]
                    row = {}
                    for i, cell in enumerate(cells):
                        col_name = column_names[i]
                        if headers[i] in ['涨跌幅', '涨跌', '周涨跌幅', '月涨跌幅', '涨幅', '跌幅', '涨跌幅%']:
                            if cell.startswith('-') or cell.startswith('↓'):
                                row[col_name] = f"<font color='green'>{cell}</font>"  # 下跌用绿色
                            elif cell.startswith('+') or cell.startswith('↑') or cell == '涨停' or cell == '大涨':
                                row[col_name] = f"<font color='red'>{cell}</font>"  # 上涨用红色
                            else:
                                row[col_name] = cell
                        else:
                            row[col_name] = cell
                    rows.append(row)

            # 计算行高
            max_cell_length = max(len(row.get(col_name, '')) for row in rows for col_name in column_names)
            if max_cell_length > 100:
                row_height = "124px"
            elif max_cell_length > 50:
                row_height = "high"
            elif max_cell_length > 30:
                row_height = "middle"
            else:
                row_height = "low"

            table_data = {
                "tag": "table",
                "page_size": min(len(rows), 10),
                "row_height": row_height,
                "header_style": {
                    "text_align": "left",
                    "text_size": "normal",
                    "background_style": "grey",
                    "text_color": "grey",
                    "bold": True
                },
                "columns": columns,
                "rows": rows
            }
            content_blocks.append(('table', table_data))
            last_end = match.end()

        # 添加最后一个表格后的文本
        text_after = section[last_end:].strip()
        if text_after:
            content_blocks.append(('text', text_after))

        # 按顺序添加元素
        for block_type, block_data in content_blocks:
            if block_type == 'table' and table_count < 5:
                elements.append(block_data)
                table_count += 1
            elif block_type == 'text':
                text_content = convert_content_without_tables(block_data)
                # 去掉章节标题（已单独添加）
                text_content = re.sub(r'^\n?\*?\*?【.+?\】\*?\*?\n?', '', text_content)
                if text_content.strip():
                    if len(text_content) > max_length:
                        paragraphs = text_content.split('\n\n')
                        current_chunk = ""
                        for para in paragraphs:
                            if len(current_chunk) + len(para) < max_length:
                                current_chunk += para + '\n\n'
                            else:
                                if current_chunk.strip():
                                    elements.append({
                                        "tag": "div",
                                        "text": {
                                            "tag": "lark_md",
                                            "content": current_chunk.strip()
                                        }
                                    })
                                current_chunk = para + '\n\n'
                        if current_chunk.strip():
                            elements.append({
                                "tag": "div",
                                "text": {
                                    "tag": "lark_md",
                                    "content": current_chunk.strip()
                                }
                            })
                    else:
                        elements.append({
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": text_content.strip()
                            }
                        })

    # 构建卡片消息 - 标题用报告标题
    card_title = report_title if report_title else f"A股{report_type}"

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": card_title
                },
                "template": "blue"
            },
            "elements": elements
        }
    }

    # 添加底部说明
    card['card']['elements'].append({"tag": "hr"})
    card['card']['elements'].append({
        "tag": "note",
        "elements": [
            {
                "tag": "plain_text",
                "content": "数据来源: 腾讯/新浪/东财 API + Tavily 财经新闻"
            }
        ]
    })

    return card


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
    use_card: bool = True,
    full_report: bool = False
) -> list:
    """
    推送到所有配置的飞书 Webhook

    Args:
        content: Markdown 报告内容
        config: 配置字典
        use_card: 是否使用卡片消息（True）或简单文本（False）
        full_report: 是否发送完整报告（True）或摘要（False）

    Returns:
        推送结果列表
    """
    results = []

    # 提取报告信息
    metadata = extract_report_metadata(content)

    # 构建消息
    if full_report:
        # 发送完整报告
        message = build_full_report_message(content, metadata)
    elif use_card:
        # 发送卡片摘要
        summary = extract_market_summary(content)
        sectors = extract_top_sectors(content)
        watchlist = extract_watchlist_summary(content)
        message = build_feishu_card(metadata, summary, sectors, watchlist)
    else:
        # 发送简单文本摘要
        summary = extract_market_summary(content)
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
    parser.add_argument("--full", action="store_true", help="发送完整报告而非摘要")

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
        results = push_to_all_feishu(content, config, use_card=not args.simple, full_report=args.full)

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