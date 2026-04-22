#!/usr/bin/env python3
"""
报告验证脚本 - 动态解析模板中的规则
支持日报(daily.md)、周报(weekly.md)、月报(monthly.md)

使用方法：
    python3 validate_report.py <report.md> [--type daily|weekly|monthly]

验证脚本会自动检测报告类型，或通过 --type 参数指定。
从对应的模板文件解析元数据进行验证。
"""
import sys
import re
import argparse
from pathlib import Path

# 模板类型映射
TEMPLATE_FILES = {
    'daily': 'daily.md',
    'weekly': 'weekly.md',
    'monthly': 'monthly.md'
}


def get_template_path(report_type: str = 'daily') -> str:
    """获取对应报告类型的模板路径"""
    script_dir = Path(__file__).parent
    template_file = TEMPLATE_FILES.get(report_type, 'daily.md')
    return str(script_dir / 'references' / template_file)


def detect_report_type(report_content: str) -> str:
    """根据报告标题/内容检测报告类型"""
    first_line = report_content.strip().split('\n')[0]
    
    # 检测周报模式
    if re.search(r'周度复盘|周报', first_line) or '本周' in report_content:
        return 'weekly'
    
    # 检测月报模式
    if re.search(r'月度复盘|月报', first_line) or '本月' in report_content:
        return 'monthly'
    
    return 'daily'


def get_unified_meta_path() -> str:
    """获取统一验证元数据文件路径"""
    script_dir = Path(__file__).parent
    return str(script_dir / 'references' / 'validation_meta.md')


def parse_unified_meta() -> dict:
    """从统一元数据文件解析通用规则"""
    unified_path = get_unified_meta_path()
    if not Path(unified_path).exists():
        return {'forbidden': [], 'report_type_rules': {}}

    with open(unified_path, 'r', encoding='utf-8') as f:
        content = f.read()

    meta = {'forbidden': [], 'report_type_rules': {}}

    # 解析禁止模式
    forbidden_match = re.search(r'FORBIDDEN:\n(.*?)FORBIDDEN:END', content, re.DOTALL)
    if forbidden_match:
        for line in forbidden_match.group(1).strip().split('\n'):
            line = line.strip()
            if line:
                meta['forbidden'].append(line)

    # 解析报告类型检测规则（可选）
    rules_match = re.search(r'REPORT_TYPE_RULES:\n(.*?)REPORT_TYPE_RULES:END', content, re.DOTALL)
    if rules_match:
        for line in rules_match.group(1).strip().split('\n'):
            if ':' in line:
                key, pattern = line.split(':', 1)
                meta['report_type_rules'][key.strip()] = pattern.strip()

    return meta


def parse_template_meta(template_path: str, report_type: str = None) -> dict:
    """从模板文件解析元数据"""
    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    meta = {
        'sections': [],
        'forbidden': [],
        'data_deps': {}
    }
    
    # 优先解析统一格式 SECTIONS:
    sections_match = re.search(r'SECTIONS:\n(.*?)SECTIONS:END', content, re.DOTALL)
    
    # 如果统一格式不存在，尝试按报告类型解析
    if not sections_match and report_type:
        type_key = f'SECTIONS_{report_type.upper()}'
        sections_match = re.search(rf'{type_key}:\n(.*?){type_key}:END', content, re.DOTALL)
    
    # 如果仍未找到，尝试解析日报格式（daily.md 默认）
    if not sections_match:
        sections_match = re.search(r'SECTIONS_DAILY:\n(.*?)SECTIONS_DAILY:END', content, re.DOTALL)
    
    if sections_match:
        for line in sections_match.group(1).strip().split('\n'):
            if '|' in line:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 2:
                    meta['sections'].append({
                        'title': parts[0],
                        'required': parts[1] == 'required' if len(parts) > 1 else True,
                        'format': parts[2] if len(parts) > 2 else None
                    })
    
    # 解析禁止模式
    forbidden_match = re.search(r'FORBIDDEN:\n(.*?)FORBIDDEN:END', content, re.DOTALL)
    if forbidden_match:
        for line in forbidden_match.group(1).strip().split('\n'):
            line = line.strip()
            if line:
                meta['forbidden'].append(line)
    
    # 解析数据依赖
    deps_match = re.search(r'DATA_DEPS:\n(.*?)DATA_DEPS:END', content, re.DOTALL)
    if deps_match:
        for line in deps_match.group(1).strip().split('\n'):
            if '→' in line:
                data_key, section = line.split('→')
                meta['data_deps'][data_key.strip()] = section.strip()

    # 合并统一元数据中的禁止模式
    unified_meta = parse_unified_meta()
    if unified_meta['forbidden']:
        # 统一禁止模式优先，后接模板特定禁止模式
        meta['forbidden'] = unified_meta['forbidden'] + meta['forbidden']

    return meta


def extract_section_content(report_content: str, section_title: str) -> str:
    """提取指定章节的内容（匹配到下一个主标题或文档结束）"""
    # 匹配到下一个主标题（一、二、三...）或文档结束
    pattern = rf"## {re.escape(section_title)}(.+?)(?=## [一二三四五六七八九十]+、|$)"
    match = re.search(pattern, report_content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def validate_report(report_path: str, report_type: str = None) -> tuple:
    """验证报告完整性"""
    with open(report_path, 'r', encoding='utf-8') as f:
        report_content = f.read()
    
    # 自动检测报告类型（如果未指定）
    if not report_type:
        report_type = detect_report_type(report_content)
    
    template_path = get_template_path(report_type)
    
    # 检查模板文件是否存在
    if not Path(template_path).exists():
        return False, [f"模板文件不存在: {template_path}"], []
    
    meta = parse_template_meta(template_path, report_type)
    errors = []
    warnings = []
    
    type_names = {'daily': '日报', 'weekly': '周报', 'monthly': '月报'}
    print(f"检测报告类型: {type_names.get(report_type, report_type)}")
    print(f"模板文件: {template_path}")
    print(f"章节定义: {len(meta['sections'])} 个章节")
    
    # 检查章节存在性
    for section in meta['sections']:
        header = f"## {section['title']}"
        if header not in report_content:
            if section['required']:
                errors.append(f"缺失章节: {section['title']}")
            continue
        
        section_content = extract_section_content(report_content, section['title'])
        
        # 检查表格格式
        if section['format'] == 'table':
            if '|' not in section_content or '---' not in section_content:
                errors.append(f"章节缺少表格: {section['title']}")
        
        # 检查文本长度
        if section['format'] and 'text:min_length=' in section['format']:
            min_len = int(re.search(r'min_length=(\d+)', section['format']).group(1))
            text_len = len(section_content)
            if text_len < min_len:
                errors.append(f"章节字数不足: {section['title']} ({text_len} < {min_len})")
    
    # 检查禁止模式
    for pattern in meta['forbidden']:
        if pattern in report_content:
            errors.append(f"发现禁止内容: \"{pattern}\"")
    
    return len(errors) == 0, errors, warnings


def main():
    parser = argparse.ArgumentParser(description='报告验证脚本')
    parser.add_argument('report_path', help='报告文件路径')
    parser.add_argument('--type', choices=['daily', 'weekly', 'monthly'],
                        help='报告类型（默认自动检测）')
    
    args = parser.parse_args()
    
    # 验证文件存在
    if not Path(args.report_path).exists():
        print(f"❌ 报告文件不存在: {args.report_path}")
        sys.exit(1)
    
    print("=" * 50)
    print("报告验证脚本 (支持日报/周报/月报)")
    print("=" * 50)
    
    passed, errors, warnings = validate_report(args.report_path, args.type)
    
    print("=" * 50)
    print("验证结果")
    print("=" * 50)
    
    if errors:
        print("\n[ERROR] (必须修复):")
        for err in errors:
            print(f"  - {err}")

    if warnings:
        print("\n[WARN] (建议修复):")
        for warn in warnings:
            print(f"  - {warn}")

    if passed:
        print("\n[OK] 报告验证通过")
        print("=" * 50)
        sys.exit(0)
    else:
        print("\n[FAIL] 报告验证失败，请修复后重新验证")
        print("=" * 50)
        sys.exit(1)


if __name__ == "__main__":
    main()