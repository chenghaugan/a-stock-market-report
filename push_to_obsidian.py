#!/usr/bin/env python3
"""
Obsidian Remote REST API 推送脚本
使用 skill: obsidian-remote-api
"""

import sys
import os
import json
import argparse
import urllib.parse
import requests
import urllib3

# 禁用 SSL 警告（自签名证书）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def load_config(config_path: str = None) -> dict:
    """加载配置文件，自动检测编码（UTF-8优先，GBK兜底）"""
    if config_path is None:
        config_path = os.path.dirname(__file__) + "/config.json"

    # 尝试UTF-8
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except UnicodeDecodeError:
        # UTF-8失败，尝试GBK
        with open(config_path, 'r', encoding='gbk') as f:
            data = json.load(f)
        # GBK读取成功，自动转换为UTF-8保存
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data


def get_report_subdir(filename: str) -> str:
    """
    根据文件名判断报告类型，返回对应的子目录名
    
    Args:
        filename: 文件名（如 "2026-04-18_日报.md"）
    
    Returns:
        子目录名（如 "日报"、"周报"、"月报"），无匹配返回空字符串
    """
    if "日报" in filename:
        return "日报"
    elif "周报" in filename:
        return "周报"
    elif "月报" in filename:
        return "月报"
    return ""


def push_to_obsidian(
    content: str,
    filename: str,
    config: dict = None,
    config_path: str = None,
    subdir: str = None
) -> dict:
    """
    推送笔记到远程 Obsidian API
    
    Args:
        content: Markdown 内容
        filename: 文件名（如 "2026-04-18_日报.md"）
        config: 配置字典（可选）
        config_path: 配置文件路径（可选）
        subdir: 子目录名（可选，如 "日报"，不指定则自动从文件名推断）
    
    Returns:
        {"success": bool, "url": str, "error": str}
    """
    if config is None:
        config = load_config(config_path)
    
    api_url = config['obsidian']['api_url']
    api_key = config['obsidian']['api_key']
    vault_path = config['obsidian']['vault_path'].lstrip('/')
    
    # 自动推断子目录
    if subdir is None:
        subdir = get_report_subdir(filename)
    
    # 构建完整路径
    if subdir:
        full_path = f"{vault_path}/{subdir}"
    else:
        full_path = vault_path
    
    # 关键：中文路径必须URL编码，但不能编码 / 分隔符
    # 正确做法：分段编码后重新拼接
    segments = full_path.split('/')
    encoded_segments = [urllib.parse.quote(s, safe='') for s in segments]
    encoded_path = '/'.join(encoded_segments)
    url = f"{api_url}/vault/{encoded_path}/{filename}"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "text/markdown"
    }
    
    try:
        response = requests.put(
            url,
            headers=headers,
            data=content.encode('utf-8'),
            timeout=30,
            verify=False  # 跳过 SSL 验证（自签名证书）
        )
        
        if response.status_code in [200, 201, 204, 205]:
            return {
                "success": True,
                "url": url,
                "status": response.status_code
            }
        else:
            return {
                "success": False,
                "error": response.text,
                "status": response.status_code,
                "url": url
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "url": url
        }


def get_from_obsidian(
    filename: str,
    config: dict = None,
    config_path: str = None,
    subdir: str = None
) -> dict:
    """
    从远程 Obsidian API 读取笔记
    
    Args:
        filename: 文件名（如 "2026-04-18_日报.md"）
        config: 配置字典（可选）
        config_path: 配置文件路径（可选）
        subdir: 子目录名（可选，如 "日报"，不指定则自动从文件名推断）
    
    Returns:
        {"success": bool, "content": str, "error": str}
    """
    if config is None:
        config = load_config(config_path)
    
    api_url = config['obsidian']['api_url']
    api_key = config['obsidian']['api_key']
    vault_path = config['obsidian']['vault_path'].lstrip('/')
    
    # 自动推断子目录
    if subdir is None:
        subdir = get_report_subdir(filename)
    
    # 构建完整路径
    if subdir:
        full_path = f"{vault_path}/{subdir}"
    else:
        full_path = vault_path
    
    # 关键：中文路径必须URL编码，但不能编码 / 分隔符
    segments = full_path.split('/')
    encoded_segments = [urllib.parse.quote(s, safe='') for s in segments]
    encoded_path = '/'.join(encoded_segments)
    url = f"{api_url}/vault/{encoded_path}/{filename}"
    
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30, verify=False)

        if response.status_code == 200:
            # 强制使用UTF-8解码，避免自动检测编码错误
            content = response.content.decode('utf-8', errors='replace')
            return {
                "success": True,
                "content": content,
                "url": url
            }
        else:
            return {
                "success": False,
                "error": response.text,
                "status": response.status_code
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def list_vault(
    config: dict = None,
    config_path: str = None
) -> dict:
    """
    列出 vault 目录内容
    
    Returns:
        {"success": bool, "files": list, "error": str}
    """
    if config is None:
        config = load_config(config_path)
    
    api_url = config['obsidian']['api_url']
    api_key = config['obsidian']['api_key']
    vault_path = config['obsidian']['vault_path'].lstrip('/')
    
    # 中文路径必须URL编码，但不能编码 / 分隔符
    segments = vault_path.split('/')
    encoded_segments = [urllib.parse.quote(s, safe='') for s in segments]
    encoded_path = '/'.join(encoded_segments)
    url = f"{api_url}/vault/{encoded_path}/"
    
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30, verify=False)
        
        if response.status_code == 200:
            # 解析文件列表
            files = response.text.strip().split('\n') if response.text else []
            return {
                "success": True,
                "files": files,
                "url": url
            }
        else:
            return {
                "success": False,
                "error": response.text,
                "status": response.status_code
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def test_connection(config: dict = None, config_path: str = None) -> dict:
    """测试 API 连接"""
    if config is None:
        config = load_config(config_path)
    
    api_url = config['obsidian']['api_url']
    
    try:
        response = requests.get(f"{api_url}/", timeout=10, verify=False)
        
        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "status": data.get("status"),
                "version": data.get("manifest", {}).get("version"),
                "service": data.get("service")
            }
        else:
            return {
                "success": False,
                "error": response.text
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def main():
    parser = argparse.ArgumentParser(description="Obsidian Remote API 推送工具")
    parser.add_argument("action", choices=["push", "get", "list", "test"],
                        help="操作类型")
    parser.add_argument("--file", "-f", help="文件名")
    parser.add_argument("--content", "-c", help="内容（用于 push）")
    parser.add_argument("--content-file", help="内容文件路径（用于 push）")
    parser.add_argument("--config", help="配置文件路径")
    
    args = parser.parse_args()
    config = load_config(args.config)
    
    if args.action == "test":
        result = test_connection(config, args.config)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    
    if args.action == "list":
        result = list_vault(config, args.config)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    
    if args.action == "get":
        if not args.file:
            print("错误: 需要指定 --file")
            sys.exit(1)
        # 自动推断子目录（与 push 保持一致）
        subdir = get_report_subdir(args.file)
        result = get_from_obsidian(args.file, config, args.config, subdir)
        if result["success"]:
            print(result["content"])
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    
    if args.action == "push":
        if not args.file:
            print("错误: 需要指定 --file")
            sys.exit(1)

        if args.content_file:
            with open(args.content_file, encoding='utf-8') as f:
                content = f.read()
        elif args.content:
            content = args.content
        else:
            print("错误: 需要指定 --content 或 --content-file")
            sys.exit(1)

        result = push_to_obsidian(content, args.file, config, args.config)
        print(json.dumps(result, indent=2, ensure_ascii=False))

        if result["success"]:
            # 推送后必须验证：HTTP 204 ≠ 文件已保存
            subdir = get_report_subdir(args.file)
            verify = get_from_obsidian(args.file, config, args.config, subdir)
            if verify["success"]:
                print(f"[OK] 推送并验证成功: {args.file} ({len(verify['content'])} 字节)")
            else:
                print(f"[WARN] 推送成功但验证失败: {args.file} — GET错误: {verify.get('error')}")
                sys.exit(1)
        else:
            print(f"❌ 推送失败: {result['error']}")
            sys.exit(1)


if __name__ == "__main__":
    main()