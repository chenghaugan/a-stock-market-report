#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetchlayer.py - A股报告数据采集层（交叉验证版）
重构 run_report.py 的所有数据获取函数

设计原则：
- 所有数据源并发请求，结果统一 cross_validate()
- 每类数据有主/备源，各自独立解析格式
- 返回结构：(parsed_data, SourceQuality)，数据质量透明化
- Tavily 补充指令由 qualitylayer.py 生成，本模块只输出高质量数据
"""

import sys, os, re, json, datetime, time, decimal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

# 导入基础设施层
sys.path.insert(0, str(Path(__file__).parent))
from datafoundation import (
    DataSourceConfig, SourceQuality, FetchResult,
    curl_keeper, fetch_with_retry,
    normalize_percent, normalize_percent_multi_source,
    build_source_configs, detect_encoding, decode_response
)


# =============================================================================
# 重写 fetch_multi_async 以支持 tasks 格式
# =============================================================================

def fetch_multi_async(tasks: List[Tuple], timeout_per_source: float = 25) -> List[SourceQuality]:
    """
    多源并发请求（tasks 格式）。
    
    Args:
        tasks: [(source_name, config, url, params_dict), ...] 格式的任务列表
        timeout_per_source: 每个源的超时时间
        
    Returns:
        List[SourceQuality]: 各源的请求结果（已包含解析状态）
    """
    results = []
    
    def _fetch_one(task):
        src_name, config, url, params = task
        import subprocess
        import time
        
        # 构建完整 URL
        if params:
            param_str = "&".join([f"{k}={v}" for k, v in params.items()])
            full_url = f"{url}?{param_str}" if "?" not in url else f"{url}&{param_str}"
        else:
            full_url = url
        
        start = time.time()
        try:
            cmd = [
                "curl", "-s", "-S", "--noproxy", "*",
                "-m", str(int(timeout_per_source)),
                "-H", "Accept: text/html,application/json,*/*",
                "-H", "Accept-Encoding: gzip, deflate",
                "--compressed",
                full_url
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout_per_source + 5)
            elapsed = time.time() - start
            
            if proc.returncode == 0:
                content = decode_response(proc.stdout)
                return SourceQuality(
                    score=0.8 if content else 0.2,
                    completeness=1.0 if content else 0.0,
                    consistency=1.0,
                    reasonableness=1.0,
                    issues=[] if content else ["空响应"],
                    raw_data={"source": src_name, "response": content, "elapsed": elapsed},
                )
            else:
                err = proc.stderr.decode("utf-8", errors="ignore") if proc.stderr else "curl failed"
                return SourceQuality(
                    score=0.0,
                    completeness=0.0,
                    consistency=0.0,
                    reasonableness=0.0,
                    issues=[err],
                    raw_data={"source": src_name, "error": err},
                )
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start
            return SourceQuality(
                score=0.0,
                completeness=0.0,
                consistency=0.0,
                reasonableness=0.0,
                issues=["timeout"],
                raw_data={"source": src_name, "elapsed": elapsed},
            )
        except Exception as e:
            return SourceQuality(
                score=0.0,
                completeness=0.0,
                consistency=0.0,
                reasonableness=0.0,
                issues=[str(e)],
                raw_data={"source": src_name, "error": str(e)},
            )
    
    # 并发执行
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tasks}
        for future in as_completed(futures):
            try:
                sq = future.result()
                # 附加额外信息
                task = futures[future]
                sq.raw_data["url"] = task[2]
                results.append(sq)
            except Exception as e:
                task = futures[future]
                results.append(SourceQuality(
                    score=0.0,
                    completeness=0.0,
                    consistency=0.0,
                    reasonableness=0.0,
                    issues=[str(e)],
                    raw_data={"source": task[0], "error": str(e)},
                ))
    
    return results


def get_response(sq: SourceQuality) -> str:
    """从 SourceQuality.raw_data 中提取响应内容"""
    return sq.raw_data.get("response", "") if sq.raw_data else ""


# =============================================================================
# 工具函数
# =============================================================================

def safe_float(val, default=0.0):
    try:
        return float(val) if val not in (None, "", "None", "--") else default
    except (ValueError, TypeError):
        return default


def safe_int_str(val):
    """安全转换整数，精度问题处理"""
    if val is None or val == "" or val == "--":
        return ""
    try:
        d = decimal.Decimal(str(val))
        return str(d.to_integral_value())
    except (decimal.InvalidOperation, ValueError, TypeError):
        return ""


def parse_tencent_index(content: str, name_map: Dict[str, str]) -> List[Dict]:
    """
    解析腾讯 qt.gtimg.cn 指数响应
    格式: v_sh000001="1~上证指数~000001~4051.43~...~涨跌幅%~
    parts[3]=当前价, parts[4]=昨收, parts[32]=涨跌幅%
    """
    results = []
    for line in content.strip().split("\n"):
        if '"' not in line or '~' not in line:
            continue
        parts = line.split("~")
        if len(parts) < 33:
            continue
        code = parts[2]  # "000001"
        if code not in name_map:
            continue
        price = safe_float(parts[3])
        prev  = safe_float(parts[4])
        pct   = safe_float(parts[32])
        # 成交额从 parts[35] 复合字段 /parts[72]
        amount = ""
        if len(parts) > 35 and parts[35] and '/' in parts[35]:
            sub = parts[35].split('/')
            if len(sub) >= 3:
                try:
                    amt = float(sub[2])
                    amount = f"{amt/1e8:.2f}亿"
                except:
                    pass
        if not amount and len(parts) > 72 and parts[72]:
            try:
                amt = float(parts[72])
                amount = f"{amt/1e8:.2f}亿"
            except:
                pass
        results.append({
            "name":       name_map[code],
            "code":       code,
            "price":      price,
            "prev_close": prev,
            "change_pct": pct,
            "amount":     amount,
        })
    return results


def parse_sina_index(content: str, name_map: Dict[str, str]) -> List[Dict]:
    """
    解析新浪 hq.sinajs.cn 指数响应
    格式: hq_str_sh000001="上证指数,当前价,涨跌幅%,..."
    fields[1]=当前价, fields[2]=涨跌幅（已%），fields[0]=名称
    """
    results = []
    for line in content.strip().split("\n"):
        if '="' not in line:
            continue
        m = re.search(r'hq_str_(\w+)="([^"]+)"', line)
        if not m:
            continue
        code_raw = m.group(1)  # "sh000001"
        fields = m.group(2).split(",")
        if len(fields) < 4:
            continue
        # 找反向映射
        code_key = code_raw.replace("s_", "")  # "sh000001" or "sz399001"
        for k, v in name_map.items():
            if code_raw.endswith(k):
                name = v
                break
        else:
            continue
        price = safe_float(fields[1])
        pct   = safe_float(fields[2])
        prev  = price / (1 + pct/100) if pct != 0 and price > 0 else 0
        results.append({
            "name":       name,
            "code":       code_key,
            "price":      price,
            "prev_close": round(prev, 2),
            "change_pct": pct,
            "amount":     "",
        })
    return results


def parse_em_index(content: str, name_map: Dict[str, str]) -> List[Dict]:
    """
    解析东方财富 push2 ulist.np 指数响应
    字段: f2=最新价, f3=涨跌幅%, f12=代码, f14=名称
    """
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return []
    results = []
    for item in obj.get("data", {}).get("diff", []):
        code = item.get("f12", "")
        if code not in name_map:
            continue
        results.append({
            "name":       name_map[code],
            "code":       code,
            "price":      safe_float(item.get("f2")),
            "prev_close": 0,
            "change_pct": safe_float(item.get("f3")),
            "amount":     "",
        })
    return results


# =============================================================================
# 指数数据 fetch_multi_index
# =============================================================================

# 腾讯指数名称映射
INDEX_NAME_MAP = {
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
    "000300": "沪深300",
}

# 指数代码
INDEX_CODES_TECENT = "sh000001,sz399001,sz399006,sh000300"


def fetch_multi_index() -> Tuple[List[Dict], List[SourceQuality]]:
    """
    三源并发获取大盘指数，交叉验证。
    返回: (parsed_data_list, source_qualities)
    """
    sources = build_source_configs()

    # 并发请求三个源
    tasks = [
        (
            "腾讯",
            sources["腾讯"],
            f"http://qt.gtimg.cn/q={INDEX_CODES_TECENT}",
            None
        ),
        (
            "新浪",
            sources["新浪"],
            "https://hq.sinajs.cn/list=s_sh000001,s_sz399001,s_sz399006,s_sh000300",
            None
        ),
        (
            "东财",
            sources["东财"],
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            {
                "fltt": "2", "invt": "2",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fields": "f2,f3,f12,f14",
                "secids": "1.000001,0.399001,0.399006,1.000300",
                "_": str(int(datetime.datetime.now().timestamp() * 1000)),
            }
        ),
    ]

    raw_results = fetch_multi_async(tasks, timeout_per_source=20)

    # 解析每个源
    parsed_by_source = {}
    for sq in raw_results:
        if sq.score < 0.3:
            continue
        try:
            src = sq.raw_data.get("source", "") if sq.raw_data else ""
            resp = sq.raw_data.get("response", "") if sq.raw_data else ""
            if src == "腾讯":
                parsed_by_source["腾讯"] = parse_tencent_index(resp, INDEX_NAME_MAP)
            elif src == "新浪":
                parsed_by_source["新浪"] = parse_sina_index(resp, INDEX_NAME_MAP)
            elif src == "东财":
                parsed_by_source["东财"] = parse_em_index(resp, INDEX_NAME_MAP)
            # 更新 raw_data 中的 parsed 字段
            if sq.raw_data:
                sq.raw_data["parsed"] = parsed_by_source.get(src, [])
        except Exception as e:
            sq.issues.append(f"解析失败: {e}")
            sq.score = 0.1

    # 交叉验证：按指数名分组对比 change_pct
    final = []
    index_names = list(INDEX_NAME_MAP.values())

    for idx_name in index_names:
        index_data = {}  # {source: {change_pct, price, ...}}
        for src, items in parsed_by_source.items():
            for item in items:
                if item["name"] == idx_name:
                    index_data[src] = item
                    break

        if not index_data:
            # 所有源都失败
            final.append({
                "name": idx_name, "price": 0, "prev_close": 0,
                "change_pct": 0, "amount": "",
                "quality_source": "none",
                "quality_score": 0.0,
                "quality_issues": ["所有数据源均失败"],
            })
            continue

        # 取价格数据（优先腾讯，腾讯失败用其他）
        best = None
        for src in ["腾讯", "新浪", "东财"]:
            if src in index_data:
                best = index_data[src]
                best["quality_source"] = src
                break

        # 交叉验证 change_pct
        pct_values = [(src, d["change_pct"]) for src, d in index_data.items()]
        if len(pct_values) >= 2:
            vals = [v for _, v in pct_values]
            diff = max(vals) - min(vals)
            if diff < 0.1:
                best["quality_score"] = 1.0
                best["quality_issues"] = []
            elif diff < 0.3:
                best["quality_score"] = 0.8
                best["quality_issues"] = [f"两源差值{diff:.2f}%"]
            elif diff < 1.0:
                best["quality_score"] = 0.6
                best["quality_issues"] = [f"多源差异{diff:.2f}%"]
            else:
                best["quality_score"] = 0.4
                best["quality_issues"] = [f"差异大({diff:.2f}%)，已用腾讯为主"]
                # 差异大时强制用腾讯数据（如果腾讯有的话）
                if "腾讯" in index_data:
                    best = index_data["腾讯"]
                    best["quality_source"] = "腾讯(争议覆盖)"
        elif len(pct_values) == 1:
            best["quality_score"] = 0.7
            best["quality_issues"] = ["单源数据"]
        else:
            best["quality_score"] = 0.0
            best["quality_issues"] = ["解析失败"]

        final.append(best)

    return final, raw_results


# =============================================================================
# 涨停板 fetch_multi_zt_pool
# =============================================================================

def parse_em_zt(content: str) -> List[Dict]:
    """
    解析东方财富 push2 涨停板响应
    f3 = 涨跌幅%（直接百分比）, f12=代码, f14=名称, f20=成交量, f62=成交额
    """
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return []
    stocks = []
    for item in obj.get("data", {}).get("diff", []):
        change = safe_float(item.get("f3"))
        if change < 9.9:
            continue
        stocks.append({
            "name":   item.get("f14", ""),
            "code":   item.get("f12", ""),
            "change": change,
            "volume": safe_int_str(item.get("f20")) if item.get("f20") not in (None, "") else "",
            "amount": safe_int_str(item.get("f62")) if item.get("f62") not in (None, "") else "",
        })
    return stocks


def parse_tencent_zt_prices(codes: List[str], raw_stocks: List[Dict]) -> List[Dict]:
    """
    腾讯 API 批量获取涨停股准确涨幅（备援层）
    已知问题：新浪 ZT API 涨幅数据失真，需要腾讯兜底
    """
    if not raw_stocks:
        return raw_stocks

    # 构建腾讯代码格式
    results = []
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]
        codes_str = ",".join([
            code if code.startswith(("sh", "sz", "bj")) else
            ("sz" + code) if code.startswith(("0", "3")) else
            ("sh" + code)
            for code in batch
        ])
        try:
            url = f"http://qt.gtimg.cn/q={codes_str}"
            result = fetch_with_retry(
                url,
                DataSourceConfig(name="腾讯", base_url="http://qt.gtimg.cn",
                                 timeout=15, response_encoding="utf-8")
            )
            if not result.success:
                results.extend(batch)
                continue
            pct_map = {}
            for line in result.response.strip().split("\n"):
                if not line.strip() or '~' not in line:
                    continue
                parts = line.split("~")
                if len(parts) < 33:
                    continue
                code_raw = parts[2]
                try:
                    price = safe_float(parts[3])
                    prev  = safe_float(parts[4])
                    pct   = (price - prev) / prev * 100 if prev else 0
                    pct_map[code_raw] = round(pct, 2)
                except (ValueError, ZeroDivisionError):
                    pass
            # 更新涨幅
            for stock in raw_stocks:
                code_key = stock["code"].replace("sh", "").replace("sz", "").replace("bj", "")
                real_pct = pct_map.get(code_key)
                if real_pct is not None:
                    stock = dict(stock)
                    stock["change"] = real_pct
                results.append(stock)
        except Exception:
            results.extend(batch)

    return results


def fetch_multi_zt_pool() -> Tuple[List[Dict], List[SourceQuality]]:
    """
    涨停板三源并发：
    - 主源：东财 push2（提供真实涨幅）
    - 备援1：新浪 ZT 板块（仅提供股票列表，无准确涨幅）
    - 备援2：腾讯单股批量（用于修正新浪涨幅）
    """
    sources = build_source_configs()
    tasks = [
        (
            "东财涨停",
            sources["东财"],
            "http://push2.eastmoney.com/api/qt/clist/get",
            {
                "pn": "1", "pz": "500", "po": "1", "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2", "invt": "2", "fid": "f3",
                "fs": "m:0+t:6,m:0+t:13,m:0+t:14,m:1+t:2,m:1+t:23",
                "fields": "f2,f3,f4,f12,f14,f20,f62",
                "_": str(int(datetime.datetime.now().timestamp() * 1000)),
            }
        ),
    ]

    raw_results = fetch_multi_async(tasks, timeout_per_source=25)

    final = []
    best_quality = None

    for sq in raw_results:
        if sq.score < 0.3:
            continue
        try:
            src = sq.raw_data.get("source", "") if sq.raw_data else ""
            resp = sq.raw_data.get("response", "") if sq.raw_data else ""
            if "东财" in src:
                stocks = parse_em_zt(resp)
                if stocks:
                    best_quality = sq
                    final = stocks
                    break
        except Exception as e:
            sq.issues.append(f"解析失败: {e}")

    # 东财完全失败，尝试新浪 ZT + 腾讯涨幅兜底
    if not final:
        try:
            sina_url = "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php"
            result = fetch_with_retry(
                sina_url + "?param=zt",
                sources["新浪"]
            )
            if result.success and "S_Finance_bankuai_zt" in result.response:
                m = re.search(
                    r'var\s+S_Finance_bankuai_zt\s*=\s*\{(.*?)\}\s*;?\s*$',
                    result.response, re.DOTALL
                )
                if m:
                    obj = json.loads("{" + m.group(1) + "}")
                    raw_stocks = []
                    for v in obj.values():
                        parts = v.split(",")
                        if len(parts) < 13:
                            continue
                        code = parts[8].strip()
                        name = parts[12].strip()
                        if code and code not in ("0", "--"):
                            raw_stocks.append({
                                "name": name, "code": code,
                                "change": 0.0,  # 涨幅待腾讯补充
                                "volume": parts[6].strip() if len(parts) > 6 else "",
                                "amount": parts[7].strip() if len(parts) > 7 else "",
                            })
                    if raw_stocks:
                        codes = [s["code"] for s in raw_stocks]
                        final = parse_tencent_zt_prices(codes, raw_stocks)
                        best_quality = SourceQuality(
                            score=0.6,
                            issues=["东财失败，使用新浪+腾讯涨幅兜底"],
                            raw_data={"source": "新浪+腾讯涨幅", "parsed": final}
                        )
        except Exception:
            pass

    if not final:
        final = []
        best_quality = SourceQuality(
            score=0.0,
            issues=["所有涨停数据源均失败"],
            raw_data={"source": "none", "parsed": []}
        )

    return final, [best_quality]


# =============================================================================
# A股自选 fetch_multi_watchlist_a
# =============================================================================

def parse_tencent_a(content: str) -> List[Dict]:
    """
    解析腾讯 web.sqt.gtimg.cn A股响应
    字段索引（~分隔）：
    [1] 股票名称, [2] 代码, [3] 当前价, [4] 昨收价
    [31] 涨跌点, [32] 涨跌幅%, [33] 最高价, [34] 最低价
    """
    results = []
    for line in content.strip().split("\n"):
        if not line.strip() or '"' not in line or '~' not in line:
            continue
        parts = line.split("~")
        if len(parts) < 35:
            continue
        name  = parts[1]
        code  = parts[2]
        price = safe_float(parts[3])
        prev  = safe_float(parts[4])
        # 直接使用腾讯 API 的涨跌幅字段 [32]
        pct   = safe_float(parts[32]) if len(parts) > 32 else (price - prev) / prev * 100 if prev else 0
        # 统一代码格式：去掉前缀（sh/sz），保留纯6位代码
        pure_code = code.replace("sh", "").replace("sz", "").replace("bj", "")
        results.append({
            "code":   pure_code,
            "name":   name,
            "price":  price,
            "prev":   round(prev, 2),
            "pct":    round(pct, 2),
            "volume": parts[6] if len(parts) > 6 else "",
            "amount": parts[37] if len(parts) > 37 else "",
            "sector": "",
            "_source": "腾讯",
        })
    return results


def parse_sina_a(content: str) -> List[Dict]:
    """解析新浪 hq.sinajs.cn A股响应"""
    results = []
    for line in content.strip().split("\n"):
        m = re.search(r'(\w{2}\d{6})="([^"]+)"', line)
        if not m:
            continue
        fields = m.group(2).split(",")
        if len(fields) < 10:
            continue
        name  = fields[0]
        price = safe_float(fields[3])
        prev  = safe_float(fields[2])
        pct   = (price - prev) / prev * 100 if prev else 0
        results.append({
            "code":   m.group(1),
            "name":   name,
            "price":  price,
            "prev":   round(prev, 2),
            "pct":    round(pct, 2),
            "volume": fields[8] if len(fields) > 8 else "",
            "amount": fields[9] if len(fields) > 9 else "",
            "sector": "",
        })
    return results


def parse_em_a(content: str) -> List[Dict]:
    """解析东方财富 push2delay ulist.np A股响应"""
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return []
    results = []
    for item in obj.get("data", {}).get("diff", []):
        code  = str(item.get("f12", ""))
        name  = item.get("f14", "")
        price = safe_float(item.get("f2"))
        pct   = safe_float(item.get("f3"))
        prev  = price / (1 + pct/100) if pct != 0 and price > 0 else 0
        vol   = item.get("f20", 0) or 0
        amt   = item.get("f62", 0) or 0
        results.append({
            "code":   ("sz" if not code.startswith(("sh", "sz")) else "") + code,
            "name":   name,
            "price":  price,
            "prev":   round(prev, 2),
            "pct":    round(pct, 2),
            "volume": safe_int_str(vol) if vol else "",
            "amount": safe_int_str(amt) if amt else "",
            "sector": "",
        })
    return results


def build_a_secids(codes: List[str]) -> str:
    """将 A 股代码列表转为东财 secids 字符串"""
    secids = []
    for code in codes:
        code = code.strip()
        if code.startswith(("sh", "sz", "bj")):
            prefix = code[:2]
            num = code[2:].zfill(6)
        else:
            num = code.zfill(6)
            prefix = "sh" if num.startswith(("6", "5", "7")) else "sz"
        secid_prefix = "1" if prefix == "sh" else "0"
        secids.append(f"{secid_prefix}.{num}")
    return ",".join(secids)


def fetch_multi_watchlist_a(codes: List[str]) -> Tuple[List[Dict], List[SourceQuality]]:
    """
    A股自选三源并发 + 单股价格交叉验证
    交叉验证维度：price（不同源的价格应接近）
    """
    if not codes:
        return [], []

    sources = build_source_configs()

    # 腾讯需要分批（每批最多50个）
    all_tencent_tasks = []
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]
        codes_str = ",".join([
            code if code.startswith(("sh", "sz", "bj")) else
            ("sz" if code.startswith(("0", "3", "4", "8")) else "sh") + code.zfill(6)
            for code in batch
        ])
        all_tencent_tasks.append(
            ("腾讯A", sources["腾讯"],
             f"http://qt.gtimg.cn/q={codes_str}", None)
        )

    # 新浪一批
    sina_codes = ",".join([
        code if code.startswith(("sh", "sz", "bj")) else
        ("sh" if code.startswith(("5", "9")) else "sz") + code.zfill(6)
        for code in codes
    ])
    sina_task = (
        "新浪A", sources["新浪"],
        f"https://hq.sinajs.cn/list={sina_codes}", None
    )

    # 东财一批
    em_secids = build_a_secids(codes)
    em_task = (
        "东财A", sources["东财"],
        "https://push2delay.eastmoney.com/api/qt/ulist.np/get",
        {
            "fltt": "2", "invt": "2",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fields": "f2,f3,f4,f12,f14,f15,f16,f17,f18,f20,f62",
            "secids": em_secids,
            "_": str(int(datetime.datetime.now().timestamp() * 1000)),
        }
    )

    all_tasks = all_tencent_tasks + [sina_task, em_task]
    raw_results = fetch_multi_async(all_tasks, timeout_per_source=25)

    # 解析
    parsed_by_source = {}
    for sq in raw_results:
        if sq.score < 0.3:
            continue
        try:
            src = sq.raw_data.get("source", "") if sq.raw_data else ""
            resp = sq.raw_data.get("response", "") if sq.raw_data else ""
            if "腾讯A" in src:
                # 腾讯分多批，合并
                key = "腾讯A"
                if key not in parsed_by_source:
                    parsed_by_source[key] = []
                parsed_by_source[key].extend(parse_tencent_a(resp))
            elif "新浪A" in src:
                parsed_by_source["新浪A"] = parse_sina_a(resp)
            elif "东财A" in src:
                parsed_by_source["东财A"] = parse_em_a(resp)
            if sq.raw_data:
                sq.raw_data["parsed"] = parsed_by_source.get(src, [])
        except Exception as e:
            sq.issues.append(f"解析失败: {e}")
            sq.score = 0.1

    # 按 code 合并多源（统一使用纯6位代码格式）
    all_codes = set()
    for items in parsed_by_source.values():
        for item in items:
            # 统一代码格式：去掉前缀，保留纯代码
            pure_code = item["code"].replace("sh", "").replace("sz", "").replace("bj", "")
            item["code"] = pure_code  # 修正回纯代码格式
            all_codes.add(pure_code)

    final = []
    for code in all_codes:
        price_by_src = {}
        pct_by_src = {}
        best_item = None
        sources_found = []

        for src, items in parsed_by_source.items():
            for item in items:
                if item["code"] == code:
                    price_by_src[src] = item["price"]
                    pct_by_src[src] = item["pct"]
                    sources_found.append(src)
                    # 优先选择腾讯数据（涨跌幅准确）
                    if best_item is None or src == "腾讯A":
                        best_item = item.copy()
                    break

        # 交叉验证价格
        prices = list(price_by_src.values())
        price_diff_pct = 0.0
        if len(prices) >= 2 and prices[0] > 0:
            price_diff_pct = abs(prices[0] - prices[1]) / prices[0] * 100

        if price_diff_pct < 0.5:
            score = 0.9
            issues = []
        elif price_diff_pct < 2.0:
            score = 0.7
            issues = [f"价格差异{price_diff_pct:.2f}%"]
        else:
            score = 0.4
            issues = [f"价格差异大({price_diff_pct:.2f}%)"]

        if best_item:
            best_item["_price_diff_pct"] = round(price_diff_pct, 3)
            best_item["_quality_score"] = score
            best_item["_quality_issues"] = issues
            best_item["_sources"] = sources_found
            final.append(best_item)

    return final, raw_results


# =============================================================================
# 港股自选 fetch_multi_watchlist_hk
# =============================================================================

def parse_tencent_hk(content: str) -> List[Dict]:
    """解析腾讯 qt.gtimg.cn 港股响应"""
    results = []
    for line in content.strip().split("\n"):
        if not line.strip() or '"' not in line or '~' not in line:
            continue
        parts = line.split("~")
        if len(parts) < 11:
            continue
        name  = parts[1]
        code  = parts[2]   # "00700"
        price = safe_float(parts[3])
        prev  = safe_float(parts[4])
        pct   = (price - prev) / prev * 100 if prev else 0
        results.append({
            "code":   f"hk{code.zfill(5)}",
            "name":   name,
            "price":  price,
            "prev":   round(prev, 2),
            "pct":    round(pct, 2),
            "volume": parts[36] if len(parts) > 36 else "",
            "amount": parts[37] if len(parts) > 37 else "",
            "sector": "",
        })
    return results


def parse_sina_hk(content: str) -> List[Dict]:
    """解析新浪 hq.sinajs.cn 港股响应"""
    results = []
    for line in content.strip().split("\n"):
        if "hq_str_hk" not in line or '"' not in line:
            continue
        try:
            code_raw = re.search(r"hq_str_hk(\d+)", line)
            if not code_raw:
                continue
            code = code_raw.group(1).zfill(5)
            start = line.index('"') + 1
            end = line.rindex('"')
            fields = line[start:end].split(",")
            if len(fields) < 9:
                continue
            name  = fields[1]
            price = safe_float(fields[6])
            prev  = safe_float(fields[3])
            pct   = safe_float(fields[8])
            results.append({
                "code":   f"hk{code}",
                "name":   name,
                "price":  price,
                "prev":   round(prev, 2),
                "pct":    round(pct, 2),
                "volume": fields[12] if len(fields) > 12 else "",
                "amount": fields[11] if len(fields) > 11 else "",
                "sector": "",
            })
        except (ValueError, IndexError):
            continue
    return results


def parse_em_hk(content: str) -> List[Dict]:
    """解析东方财富 push2delay ulist.np 港股响应"""
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return []
    results = []
    for item in obj.get("data", {}).get("diff", []):
        code  = str(item.get("f12", "")).zfill(5)
        name  = item.get("f14", "")
        price = safe_float(item.get("f2"))
        pct   = safe_float(item.get("f3"))
        prev  = price / (1 + pct/100) if pct != 0 and price > 0 else 0
        results.append({
            "code":   f"hk{code}",
            "name":   name,
            "price":  round(price, 2),
            "prev":   round(prev, 2),
            "pct":    round(pct, 2),
            "volume": "",
            "amount": "",
            "sector": "",
        })
    return results


def parse_em_hk_v2(codes: List[str]) -> List[Dict]:
    """
    东方财富 push2delay 单股循环查询（备援层）
    HK secid 格式: 116.XXXXX
    """
    results = []
    for code in codes:
        code = code.strip().zfill(5)
        secid = f"116.{code}"
        try:
            result = fetch_with_retry(
                "https://push2delay.eastmoney.com/api/qt/stock/get",
                DataSourceConfig(name="东财HK", base_url="https://push2delay.eastmoney.com",
                                 timeout=15, response_encoding="utf-8"),
                params={
                    "secid": secid,
                    "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60",
                    "_": str(int(datetime.datetime.now().timestamp() * 1000)),
                }
            )
            if not result.success:
                continue
            obj = json.loads(result.response)
            item_data = obj.get("data", {})
            if not item_data:
                continue
            price = safe_float(item_data.get("f43", 0)) / 100
            prev  = safe_float(item_data.get("f60", 0)) / 100
            low   = safe_float(item_data.get("f45", 0)) / 100
            high  = safe_float(item_data.get("f46", 0)) / 100
            pct   = (price - prev) / prev * 100 if prev > 0 else 0
            vol   = item_data.get("f47", 0) or 0
            amt   = item_data.get("f48", 0) or 0
            results.append({
                "code":   f"hk{code}",
                "name":   item_data.get("f58", ""),
                "price":  round(price, 2),
                "prev":   round(prev, 2),
                "pct":    round(pct, 2),
                "volume": safe_int_str(vol) if vol else "",
                "amount": safe_int_str(amt) if amt else "",
                "sector": "",
            })
        except Exception:
            continue
    return results


def fetch_multi_watchlist_hk(codes: List[str]) -> Tuple[List[Dict], List[SourceQuality]]:
    """
    港股自选最多4层备援：
    腾讯 → 新浪 → 东财ulist → 东财单股循环
    交叉验证：price
    """
    if not codes:
        return [], []

    sources = build_source_configs()
    hk_codes = [c.strip() for c in codes if c.strip()]

    # 腾讯分批
    tencent_tasks = []
    for i in range(0, len(hk_codes), 50):
        batch = hk_codes[i:i+50]
        codes_str = ",".join([
            c if c.startswith("hk") else f"hk{c.zfill(5)}"
            for c in batch
        ])
        tencent_tasks.append(
            ("腾讯HK", sources["腾讯"],
             f"http://qt.gtimg.cn/q={codes_str}", None)
        )

    # 新浪
    sina_codes_str = ",".join([f"hk{c.zfill(5)}" for c in hk_codes])
    sina_task = (
        "新浪HK", sources["新浪"],
        f"https://hq.sinajs.cn/rn=xxx&list={sina_codes_str}", None
    )

    # 东财ulist
    em_secids = ",".join([f"116.{c.strip().zfill(5)}" for c in hk_codes])
    em_task = (
        "东财HKulist", sources["东财"],
        "https://push2delay.eastmoney.com/api/qt/ulist.np/get",
        {
            "fltt": "2", "invt": "2",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fields": "f2,f3,f4,f12,f14,f15,f16",
            "secids": em_secids,
            "_": str(int(datetime.datetime.now().timestamp() * 1000)),
        }
    )

    # 并发请求前三层
    all_tasks = tencent_tasks + [sina_task, em_task]
    raw_results = fetch_multi_async(all_tasks, timeout_per_source=25)

    # 解析
    parsed_by_source = {}
    for sq in raw_results:
        if sq.score < 0.3:
            continue
        try:
            src = sq.raw_data.get("source", "") if sq.raw_data else ""
            resp = sq.raw_data.get("response", "") if sq.raw_data else ""
            if "腾讯HK" in src:
                key = "腾讯HK"
                if key not in parsed_by_source:
                    parsed_by_source[key] = []
                parsed_by_source[key].extend(parse_tencent_hk(resp))
            elif "新浪HK" in src:
                parsed_by_source["新浪HK"] = parse_sina_hk(resp)
            elif "东财HKulist" in src:
                parsed_by_source["东财HKulist"] = parse_em_hk(resp)
            if sq.raw_data:
                sq.raw_data["parsed"] = parsed_by_source.get(src, [])
        except Exception as e:
            sq.issues.append(f"解析失败: {e}")
            sq.score = 0.1

    # 合并代码
    all_hk_codes = set()
    for items in parsed_by_source.values():
        for item in items:
            all_hk_codes.add(item["code"])

    # 东财单股备援（ulist失败的股）
    failed_codes = set(codes) - all_hk_codes
    if failed_codes:
        try:
            fallback = parse_em_hk_v2(list(failed_codes))
            if fallback:
                parsed_by_source["东财HKv2"] = fallback
                all_hk_codes.update(s["code"] for s in fallback)
        except Exception:
            pass

    # 交叉验证（同A股逻辑）
    final = []
    for code in all_hk_codes:
        price_by_src = {}
        best_item = None
        for src, items in parsed_by_source.items():
            for item in items:
                if item["code"] == code:
                    price_by_src[src] = item["price"]
                    if best_item is None:
                        best_item = item.copy()
                    break

        prices = list(price_by_src.values())
        price_diff_pct = 0.0
        if len(prices) >= 2 and prices[0] > 0:
            price_diff_pct = abs(prices[0] - prices[1]) / prices[0] * 100

        if price_diff_pct < 0.5:
            score = 0.9
            issues = []
        elif price_diff_pct < 2.0:
            score = 0.7
            issues = [f"价格差异{price_diff_pct:.2f}%"]
        else:
            score = 0.4
            issues = [f"价格差异大({price_diff_pct:.2f}%)"]

        if best_item:
            best_item["_price_diff_pct"] = round(price_diff_pct, 3)
            best_item["_quality_score"] = score
            best_item["_quality_issues"] = issues
            final.append(best_item)

    return final, raw_results


# =============================================================================
# 周K/月K fetch_kline
# =============================================================================

def parse_em_kline(content: str, index_names: Dict[str, str]) -> List[Dict]:
    """
    解析东方财富 push2his K线响应
    klines: ["日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率"]
    """
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return []
    results = []
    klines_data = obj.get("data", {})
    if not klines_data:
        return []
    for secid, name in index_names.items():
        klines_str = klines_data.get(secid, {}).get("klines", [])
        if not klines_str:
            continue
        latest = klines_str[-1].split(",")
        if len(latest) < 11:
            continue
        date     = latest[0]
        open_    = safe_float(latest[1])
        close    = safe_float(latest[2])
        high     = safe_float(latest[3])
        low      = safe_float(latest[4])
        volume   = latest[5]
        amount   = latest[6]
        change_pct = safe_float(latest[8])
        change_val = safe_float(latest[9])
        if amount and amount.replace('.', '').isdigit():
            amt_float = float(amount)
            amount_fmt = f"{amt_float/1e8:.2f}亿"
        else:
            amount_fmt = ""
        results.append({
            "name":       name,
            "date":       date,
            "open":       open_,
            "close":      close,
            "high":       high,
            "low":        low,
            "volume":     volume,
            "amount":     amount_fmt,
            "change_pct": change_pct,
            "change_val": change_val,
        })
    return results


def fetch_kline(ktype: str = "weekly", count: int = 5) -> Tuple[List[Dict], List[SourceQuality]]:
    """
    获取大盘指数K线数据
    ktype: 'weekly' (klt=102) | 'monthly' (klt=103)
    """
    index_names = {
        "1.000001": "上证指数",
        "0.399001": "深证成指",
        "0.399006": "创业板指",
        "1.000300": "沪深300",
    }
    klt = "102" if ktype == "weekly" else "103"
    lmt = str(count)

    sources = build_source_configs()
    task = (
        f"东财{ktype}K",
        sources["东财"],
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        {
            "secid": ",".join(index_names.keys()),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": klt,
            "fqt": "1",
            "beg": "0",
            "end": "20500101",
            "lmt": lmt,
        }
    )

    raw_results = fetch_multi_async([task], timeout_per_source=25)
    sq = raw_results[0] if raw_results else None

    if sq and sq.score >= 0.3:
        try:
            resp = sq.raw_data.get("response", "") if sq.raw_data else ""
            parsed = parse_em_kline(resp, index_names)
            if sq.raw_data:
                sq.raw_data["parsed"] = parsed
            return parsed, raw_results
        except Exception as e:
            if sq:
                sq.issues.append(f"解析失败: {e}")
                sq.score = 0.1

    return [], raw_results


# =============================================================================
# 热门板块/个股分析（基于高质量数据）
# =============================================================================

def analyze_hot_sectors(sectors: List[Dict], top_n: int = 5) -> List[Dict]:
    """按涨幅排序取 Top N 热门板块（所有数据已归一化）"""
    if not sectors:
        return []
    sorted_sectors = sorted(sectors, key=lambda x: x.get('change', 0), reverse=True)
    result = []
    for i, s in enumerate(sorted_sectors[:top_n], 1):
        result.append({
            'rank':   i,
            'name':   s['name'],
            'change': s.get('change', 0),
            'volume': s.get('volume', ''),
            'leader': s.get('leader', ''),
            '_quality_source': s.get('_quality_source', ''),
            '_quality_score':  s.get('_quality_score', 0),
        })
    return result


def analyze_hot_stocks(zt_pool: List[Dict], top_n: int = 5) -> List[Dict]:
    """按涨幅排序取 Top N 涨停股"""
    if not zt_pool:
        return []
    sorted_stocks = sorted(zt_pool, key=lambda x: float(x.get('change', 0) or 0), reverse=True)
    result = []
    for i, s in enumerate(sorted_stocks[:top_n], 1):
        result.append({
            'rank':   i,
            'name':   s.get('name', ''),
            'code':   s.get('code', ''),
            'change': float(s.get('change', 0) or 0),
            'volume': s.get('volume', ''),
            'amount': s.get('amount', ''),
        })
    return result


# =============================================================================
# 入口测试
# =============================================================================

if __name__ == "__main__":
    print("=== fetchlayer.py 自检 ===\n")
    print("注意：需要完整 run_report.py 环境才能运行实际测试")
    print("本模块提供以下函数：")
    funcs = [
        "fetch_multi_index", "fetch_multi_zt_pool",
        "fetch_multi_watchlist_a", "fetch_multi_watchlist_hk",
        "fetch_kline",
        "analyze_hot_sectors", "analyze_hot_stocks",
    ]
    for f in funcs:
        print(f"  - {f}()")
    print("\n=== 自检完成 ===")
