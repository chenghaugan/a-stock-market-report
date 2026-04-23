#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
datafoundation.py - A股报告基础设施层（重构版）

将 run_report.py 里分散的 curl/requests 逻辑、cross_validate 逻辑、
validate_data 逻辑抽取/重写为清晰的底层模块。

功能：
- DataSourceConfig: 数据源配置（dataclass）
- SourceQuality: 数据质量评估结果（dataclass）
- CurlKeeper: 统一 curl 封装，指数退避重试，自动编码检测
- QualityScorer: 多维度数据质量评分与交叉验证

作者：重构版本
"""

from __future__ import annotations

import subprocess
import time
import re
import json
from dataclasses import dataclass, field

try:
    import chardet
    _HAS_CHARDET = True
except ImportError:
    _HAS_CHARDET = False
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# =============================================================================
# 1. DataSourceConfig 数据源配置
# =============================================================================

@dataclass
class DataSourceConfig:
    """
    数据源配置信息。

    用于描述一个数据源的基本属性和请求行为。

    Attributes:
        name: 数据源名称，如 "腾讯" / "新浪" / "东方财富"
        priority: 优先级，1=优先使用, 2=备援1, 3=备援2
        base_timeout: 基础超时秒数（首次请求使用此值，重试时指数增长）
        max_retries: 最大重试次数（指数退避：1s → 2s → 4s）
        accept_encoding: 支持的编码列表，如 ["utf-8", "gbk", "gb2312"]
    """
    name: str
    priority: int = 1
    base_timeout: float = 15.0
    max_retries: int = 3
    accept_encoding: List[str] = field(
        default_factory=lambda: ["utf-8", "gbk", "gb2312"]
    )


# =============================================================================
# 2. SourceQuality 数据质量评估结果
# =============================================================================

@dataclass
class SourceQuality:
    """
    数据质量评估结果。

    用于多源数据比对和可信度评分。分数范围 0.0~1.0。

    Attributes:
        score: 综合评分 0.0~1.0
        completeness: 数据完整度 0~1（字段填充比例）
        consistency: 多源一致性 0~1（三源对比差值）
        reasonableness: 数据合理性 0~1（价格在合理范围等）
        issues: 发现的问题列表
        raw_data: 原始数据副本（用于调试）
    """
    score: float = 0.0
    completeness: float = 1.0
    consistency: float = 1.0
    reasonableness: float = 1.0
    issues: List[str] = field(default_factory=list)
    raw_data: Optional[Dict[str, Any]] = None


@dataclass
class FetchResult:
    """
    curl 请求结果封装。

    Attributes:
        success: 请求是否成功
        content: 响应内容（已解码）
        encoding: 检测到的编码
        status_code: HTTP 状态码
        elapsed: 请求耗时（秒）
        error: 错误信息（失败时）
        source: 数据源名称
    """
    success: bool = False
    content: str = ""
    encoding: str = "utf-8"
    status_code: int = 0
    elapsed: float = 0.0
    error: str = ""
    source: str = ""


# =============================================================================
# 3. CurlKeeper 统一 curl 封装
# =============================================================================

class CurlKeeper:
    """
    统一 curl 封装类，提供指数退避重试和自动编码检测。

    特性：
    - 指数退避重试：1s → 2s → 4s
    - 代理处理：--noproxy '*'，不依赖环境代理
    - 编码自动检测：优先 response header charset，无则 chardet，fallback utf-8
    - 不可恢复错误立即放弃：400, 401, 403, 404 不重试

    Example:
        config = DataSourceConfig(name="腾讯", priority=1, base_timeout=15, max_retries=3)
        keeper = CurlKeeper(config)
        body, encoding, code = keeper.fetch("https://example.com/api")
    """

    # 不可恢复的 HTTP 状态码（立即放弃，不重试）
    UNRECOVERABLE_CODES: set[int] = {400, 401, 403, 404}

    # 可恢复的错误模式（timeout, 5xx, 502, 503）
    RECOVERABLE_PATTERNS: Tuple[str, ...] = (
        "timeout", "Connection timed out",
        "502", "503", "504",
        "Connection reset",
        "Network is unreachable",
        "Name or service not known",
    )

    # 默认请求头
    DEFAULT_HEADERS: Dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Referer": "https://finance.sina.com.cn/",
    }

    def __init__(self, config: DataSourceConfig) -> None:
        """
        初始化 CurlKeeper。

        Args:
            config: 数据源配置（包含名称、超时、重试次数等）
        """
        self.config = config
        self._headers = self._build_headers(config.name)

    def _build_headers(self, source_name: str) -> Dict[str, str]:
        """根据数据源名称构建请求头（部分源需要 Cookie）。"""
        headers = dict(self.DEFAULT_HEADERS)
        name_lower = source_name.lower()

        if "tencent" in name_lower or "腾讯" in source_name:
            headers["Cookie"] = "qgqp_b_id=xxx"
        elif "sina" in name_lower or "新浪" in source_name:
            headers["Cookie"] = "SINAGLOBAL=xxx; UOR=finance.sina.com.cn"

        return headers

    def _is_recoverable_error(self, stderr: str, http_code: int) -> bool:
        """
        判断错误是否可恢复（应重试）。

        Args:
            stderr: curl 错误输出
            http_code: HTTP 状态码

        Returns:
            True if the error is recoverable
        """
        if http_code in self.UNRECOVERABLE_CODES:
            return False

        if http_code >= 500:
            return True

        stderr_lower = stderr.lower()
        return any(p.lower() in stderr_lower for p in self.RECOVERABLE_PATTERNS)

    def _detect_encoding_from_header(self, stderr: str) -> Optional[str]:
        """
        从 curl 输出（stderr 包含 header）中提取 charset。

        Args:
            stderr: curl 的 stderr 输出

        Returns:
            检测到的编码或 None
        """
        match = re.search(r"charset\s*=\s*([^\s\r\n\"';]+)", stderr, re.IGNORECASE)
        if match:
            return match.group(1).strip().strip('"').strip("'")

        content_type_match = re.search(
            r"Content-Type:\s*[^;\r\n]*;\s*charset\s*=\s*([^\s\r\n\"';]+)",
            stderr,
            re.IGNORECASE,
        )
        if content_type_match:
            return content_type_match.group(1).strip().strip('"').strip("'")

        return None

    def _detect_encoding_from_body(self, raw_bytes: bytes) -> Optional[str]:
        """
        使用 chardet 自动检测字节流编码（如 chardet 不可用则尝试解码判断）。

        Args:
            raw_bytes: 原始字节数据

        Returns:
            检测到的编码名称或 None
        """
        if _HAS_CHARDET:
            try:
                result = chardet.detect(raw_bytes)
                encoding = result.get("encoding", "")
                if encoding and result.get("confidence", 0) > 0.7:
                    return encoding
            except Exception:
                pass

        # Fallback: 尝试解码判断
        for enc in self.config.accept_encoding:
            try:
                raw_bytes.decode(enc)
                return enc
            except (UnicodeDecodeError, LookupError):
                continue

        return None

    def fetch(
        self,
        url: str,
        encoding_hint: str = "auto",
        params: Optional[Dict[str, str]] = None,
    ) -> Tuple[Optional[bytes], str, int]:
        """
        执行 HTTP GET 请求，支持指数退避重试。

        Args:
            url: 请求 URL
            encoding_hint: 编码提示，"auto" 或具体编码如 "utf-8", "gbk"
            params: GET 请求参数（会被拼接到 URL）

        Returns:
            Tuple[response_body, detected_encoding, http_code]:
            - response_body: 原始字节数据，失败时 None
            - detected_encoding: 检测到的编码名称
            - http_code: HTTP 状态码，失败时 0
        """
        timeout = self.config.base_timeout
        params_str = self._build_params_str(params)

        for attempt in range(self.config.max_retries):
            try:
                cmd = self._build_curl_cmd(url, timeout, params_str)
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=timeout + 10,
                )

                raw_output = result.stdout
                stderr = result.stderr.decode("utf-8", errors="replace")

                # 提取 HTTP 状态码
                http_code = self._extract_http_code(stderr)

                # 空响应处理
                if not raw_output:
                    if attempt < self.config.max_retries - 1:
                        wait_time = 2.0 ** attempt
                        time.sleep(wait_time)
                        timeout *= 2
                        continue
                    return None, "utf-8", http_code

                # 不可恢复错误立即返回
                if http_code in self.UNRECOVERABLE_CODES:
                    return raw_output, "utf-8", http_code

                # 检测编码
                detected_encoding = self._detect_encoding(http_code, stderr, raw_output, encoding_hint)

                return raw_output, detected_encoding, http_code

            except subprocess.TimeoutExpired:
                if attempt < self.config.max_retries - 1:
                    wait_time = 2.0 ** attempt
                    time.sleep(wait_time)
                    timeout *= 2
                    continue
                return None, "utf-8", 0

            except Exception as e:
                if attempt < self.config.max_retries - 1:
                    wait_time = 2.0 ** attempt
                    time.sleep(wait_time)
                    timeout *= 2
                    continue
                return None, "utf-8", 0

        return None, "utf-8", 0

    def _detect_encoding(
        self,
        http_code: int,
        stderr: str,
        raw_bytes: bytes,
        encoding_hint: str,
    ) -> str:
        """
        综合多种策略检测响应编码。

        Priority:
        1. encoding_hint != "auto" → use hint
        2. HTTP header charset
        3. chardet detection
        4. config.accept_encoding
        5. utf-8

        Args:
            http_code: HTTP 状态码
            stderr: curl stderr
            raw_bytes: 原始响应字节
            encoding_hint: 编码提示

        Returns:
            检测到的编码名称
        """
        if encoding_hint != "auto":
            return encoding_hint

        # 尝试从 HTTP header 提取 charset
        header_enc = self._detect_encoding_from_header(stderr)
        if header_enc:
            try:
                raw_bytes.decode(header_enc)
                return header_enc
            except (UnicodeDecodeError, LookupError):
                pass

        # chardet 自动检测
        detected = self._detect_encoding_from_body(raw_bytes)
        if detected:
            return detected

        # 配置的编码列表
        for enc in self.config.accept_encoding:
            try:
                raw_bytes.decode(enc)
                return enc
            except (UnicodeDecodeError, LookupError):
                continue

        return "utf-8"

    def _build_params_str(self, params: Optional[Dict[str, str]]) -> str:
        """将参数字典转为 --data-urlencode 字符串。"""
        if not params:
            return ""
        parts = []
        for key, value in params.items():
            parts.append(f"--data-urlencode '{key}={value}'")
        return " ".join(parts)

    def _build_curl_cmd(
        self,
        url: str,
        timeout: float,
        params_str: str,
    ) -> List[str]:
        """构建 curl 命令行。"""
        cmd = [
            "curl", "-s", "--noproxy", "*",
            "--connect-timeout", str(int(timeout)),
            "-m", str(int(timeout)),
        ]

        for key, value in self._headers.items():
            cmd.extend(["-H", f"{key}: {value}"])

        if params_str:
            cmd.extend(params_str.split())

        cmd.append(url)
        return cmd

    def _extract_http_code(self, stderr: str) -> int:
        """从 curl stderr 输出中提取 HTTP 状态码。"""
        match = re.search(r"HTTP[\d.]*\s+(\d+)", stderr)
        if match:
            return int(match.group(1))
        return 0

    def fetch_text(
        self,
        url: str,
        encoding_hint: str = "auto",
        params: Optional[Dict[str, str]] = None,
    ) -> Tuple[Optional[str], str, int]:
        """
        获取文本响应，自动解码为字符串。

        Returns:
            Tuple[response_text, detected_encoding, http_code]
        """
        raw, encoding, code = self.fetch(url, encoding_hint, params)
        if raw is None:
            return None, encoding, code

        try:
            text = raw.decode(encoding, errors="replace")
        except (UnicodeDecodeError, LookupError):
            text = raw.decode("utf-8", errors="replace")
            encoding = "utf-8"

        return text, encoding, code

    def fetch_json(
        self,
        url: str,
        encoding_hint: str = "auto",
        params: Optional[Dict[str, str]] = None,
    ) -> Tuple[Optional[Dict[str, Any]], str, int]:
        """
        获取 JSON 响应，自动解析为 dict。

        Returns:
            Tuple[parsed_json, detected_encoding, http_code]
        """
        text, encoding, code = self.fetch_text(url, encoding_hint, params)
        if text is None:
            return None, encoding, code

        try:
            obj = json.loads(text)
            return obj, encoding, code
        except (json.JSONDecodeError, ValueError) as e:
            return None, encoding, code


# =============================================================================
# 4. QualityScorer 数据质量评分与交叉验证
# =============================================================================

class QualityScorer:
    """
    多维度数据质量评分与交叉验证。

    支持对以下数据类型评分：
    - 指数数据 (index_data)
    - 自选股/关注列表 (watchlist)

    交叉验证 (cross_validate) 对比多源数据，计算一致性分数。
    """

    # 指数数据必须字段
    INDEX_REQUIRED_FIELDS: Tuple[str, ...] = (
        "name", "code", "price", "prev_close", "change_pct"
    )

    # 单个标的价格合理范围（a股，±50%）
    PRICE_REASONABLE_RANGE: Tuple[float, float] = (0.1, 1000.0)

    def __init__(self) -> None:
        """初始化 QualityScorer。"""
        pass

    def score_index_data(self, data: Dict[str, Any], source: str) -> SourceQuality:
        """
        对指数数据质量评分。

        Args:
            data: 解析后的指数数据字典，包含 name/code/price/prev_close/change_pct
            source: 数据源名称

        Returns:
            SourceQuality 评分结果
        """
        issues: List[str] = []
        completeness = 1.0
        reasonableness = 1.0

        # 完整性检查
        for field_name in self.INDEX_REQUIRED_FIELDS:
            if field_name not in data or data[field_name] in (None, "", 0):
                issues.append(f"字段缺失或为空: {field_name}")
                completeness -= 0.15

        completeness = max(0.0, completeness)

        # 合理性检查
        price = data.get("price", 0)
        prev_close = data.get("prev_close", 0)

        if price <= 0:
            issues.append("价格异常: <=0")
            reasonableness -= 0.3

        if price < self.PRICE_REASONABLE_RANGE[0] or price > self.PRICE_REASONABLE_RANGE[1]:
            issues.append(f"价格超出合理范围: {price}")
            reasonableness -= 0.2

        if prev_close > 0:
            change_pct = data.get("change_pct", 0)
            # 涨幅合理性：单日涨跌不超过 20%
            if abs(change_pct) > 20:
                issues.append(f"涨跌幅异常: {change_pct}%")
                reasonableness -= 0.2

        reasonableness = max(0.0, reasonableness)

        # 综合评分
        score = completeness * reasonableness

        return SourceQuality(
            score=score,
            completeness=completeness,
            consistency=1.0,  # 单源评分不涉及一致性
            reasonableness=reasonableness,
            issues=issues,
            raw_data=data if isinstance(data, dict) else None,
        )

    def score_watchlist(
        self,
        data: List[Dict[str, Any]],
        source: str,
    ) -> SourceQuality:
        """
        对自选股/关注列表质量评分。

        Args:
            data: 股票列表
            source: 数据源名称

        Returns:
            SourceQuality 评分结果
        """
        issues: List[str] = []
        completeness = 1.0
        reasonableness = 1.0

        if not data:
            issues.append("空关注列表")
            return SourceQuality(
                score=0.0,
                completeness=0.0,
                consistency=1.0,
                reasonableness=0.0,
                issues=issues,
                raw_data={"items": [], "source": source},
            )

        # 检查必要字段
        required = ("name", "code")
        for field_name in required:
            empty_count = sum(1 for item in data if not item.get(field_name))
            if empty_count > 0:
                issues.append(f"{empty_count} 条数据缺少 {field_name}")
                completeness -= 0.1 * (empty_count / len(data))

        completeness = max(0.0, completeness)
        score = completeness * reasonableness

        return SourceQuality(
            score=score,
            completeness=completeness,
            consistency=1.0,
            reasonableness=reasonableness,
            issues=issues,
            raw_data={"items": data[:50], "source": source, "total": len(data)},
        )

    def cross_validate(
        self,
        results: List[Tuple[str, Any]],
    ) -> Tuple[Any, SourceQuality]:
        """
        交叉验证多源数据，计算一致性分数。

        输入格式: [("腾讯", {...}), ("新浪", {...}), ("东财", {...})]
        每个元素为 (source_name, parsed_data)

        判定逻辑:
        - 三者一致（差值 < 0.1%）→ score = 1.0
        - 两两一致 → score = 0.8
        - 差异大 → 输出警告，取中间值

        Args:
            results: 多源数据列表

        Returns:
            Tuple[best_data, overall_quality]:
            - best_data: 综合后的最佳数据
            - overall_quality: 总体质量评分（包含 consistency）
        """
        if not results:
            return None, SourceQuality(
                score=0.0,
                completeness=0.0,
                consistency=0.0,
                reasonableness=0.0,
                issues=["无数据源"],
            )

        if len(results) == 1:
            source_name, data = results[0]
            return data, SourceQuality(
                score=0.7,
                completeness=1.0,
                consistency=0.5,
                reasonableness=1.0,
                issues=["单源数据"],
                raw_data={"source": source_name, "data": data},
            )

        # 提取可比较的数值字段（change_pct）
        comparable_values = self._extract_comparable_values(results)

        if not comparable_values:
            # 无法数值比较，取最高分数据
            return self._pick_best_by_score(results)

        # 计算一致性
        consistency, diff = self._compute_consistency(comparable_values)

        # 综合评分
        overall_score = consistency
        overall_issues: List[str] = []

        if diff < 0.1:
            overall_issues.append("三源高度一致")
        elif diff < 0.3:
            overall_issues.append(f"两源一致，差值 {diff:.2f}%")
        else:
            overall_issues.append(f"多源差异大({diff:.2f}%)，已用中间值")

        # 取中间值（去极端）
        sorted_values = sorted(comparable_values, key=lambda x: x[1])
        middle = sorted_values[len(sorted_values) // 2]
        best_source = middle[0]
        best_data = self._find_data_by_source(results, best_source)

        raw_data_dict = {
            "sources": {src: val for src, val in comparable_values},
            "consistency": consistency,
            "diff_pct": diff,
        }

        return best_data, SourceQuality(
            score=overall_score,
            completeness=1.0,
            consistency=consistency,
            reasonableness=1.0,
            issues=overall_issues,
            raw_data=raw_data_dict,
        )

    def _extract_comparable_values(
        self,
        results: List[Tuple[str, Any]],
    ) -> List[Tuple[str, float]]:
        """
        从多源结果中提取可比较的 change_pct 数值。

        Returns:
            List[(source_name, change_pct_value)]
        """
        comparable: List[Tuple[str, float]] = []

        for source_name, data in results:
            if data is None:
                continue

            if isinstance(data, dict):
                change_pct = data.get("change_pct")
                if change_pct is not None:
                    try:
                        comparable.append((source_name, float(change_pct)))
                    except (ValueError, TypeError):
                        pass

            elif isinstance(data, list) and len(data) > 0:
                # 取第一条的 change_pct
                first = data[0]
                if isinstance(first, dict):
                    change_pct = first.get("change_pct")
                    if change_pct is not None:
                        try:
                            comparable.append((source_name, float(change_pct)))
                        except (ValueError, TypeError):
                            pass

        return comparable

    def _compute_consistency(
        self,
        values: List[Tuple[str, float]],
    ) -> Tuple[float, float]:
        """
        计算多源数据的一致性。

        Returns:
            Tuple[consistency_score (0~1), max_diff_percent]
        """
        if len(values) < 2:
            return 1.0, 0.0

        numeric_values = [v for _, v in values]
        max_val = max(numeric_values)
        min_val = min(numeric_values)

        if max_val == 0 and min_val == 0:
            return 1.0, 0.0

        # 差值百分比（相对于平均值）
        avg = sum(numeric_values) / len(numeric_values)
        if avg == 0:
            return 0.5, max_val - min_val

        diff_pct = abs(max_val - min_val) / abs(avg) * 100

        if diff_pct < 0.1:
            consistency = 1.0
        elif diff_pct < 0.3:
            consistency = 0.8
        elif diff_pct < 1.0:
            consistency = 0.6
        else:
            consistency = 0.4

        return consistency, diff_pct

    def _pick_best_by_score(
        self,
        results: List[Tuple[str, Any]],
    ) -> Tuple[Any, SourceQuality]:
        """当无法数值比较时，按数据完整性选择最佳。"""
        best_data = results[0][1] if results else None
        best_source = results[0][0] if results else "unknown"

        return best_data, SourceQuality(
            score=0.6,
            completeness=1.0,
            consistency=0.5,
            reasonableness=1.0,
            issues=["无法数值交叉验证"],
            raw_data={"source": best_source},
        )

    def _find_data_by_source(
        self,
        results: List[Tuple[str, Any]],
        source_name: str,
    ) -> Any:
        """根据源名称查找对应数据。"""
        for src, data in results:
            if src == source_name:
                return data
        return results[0][1] if results else None


# =============================================================================
# 5. 辅助工具函数
# =============================================================================

def normalize_percent(value: Any, source_name: str) -> float:
    """
    统一将各种格式的百分比值转为 float。

    问题背景：
    - 板块API（新浪/东财）直接返回百分比格式（如 5.09 表示 5.09%）
    - 涨停池API可能返回小数格式（如 0.10 表示 10%）
    - 需要根据数据源类型智能判断

    判断逻辑（2026-04-22 修复）：
    - 板块类API（含"板块"）：直接返回，不×100
    - 其他API：
      - abs(value) <= 1 → 小数格式，×100
      - abs(value) > 1 → 百分比格式，直接返回

    Args:
        value: 原始值（可能为 str 或 number）
        source_name: 数据源名称（用于判断格式）

    Returns:
        float: 归一化的百分比值（如 1.5 表示 1.5%）
    """
    try:
        if isinstance(value, str):
            value = value.strip().replace("%", "").replace("％", "")
            value = value.replace(",", "")
            num_value = float(value)
        else:
            num_value = float(value)
    except (ValueError, TypeError):
        print(f"[WARN] {source_name}: 无法解析百分比值 '{value}'，返回0")
        return 0.0

    # 板块类API（新浪板块、东财板块）：直接返回百分比格式
    if "板块" in source_name:
        # 合理性检查：涨幅应在 [-20, 20]% 范围内
        if abs(num_value) > 20:
            print(f"[WARN] {source_name}: 百分比值 {num_value}% 超出正常涨跌范围[-20%, 20%]")
        return num_value

    # 其他API：根据数值大小判断格式
    # abs(value) <= 1 → 小数格式（如 0.10 表示 10%），×100
    # abs(value) > 1 → 百分比格式（如 10.5 表示 10.5%），直接返回
    if abs(num_value) <= 1:
        result = num_value * 100
    else:
        result = num_value

    # 合理性检查
    if abs(result) > 20:
        print(f"[WARN] {source_name}: 百分比归一化后值 {result}% 超出正常涨跌范围[-20%, 20%]")

    return result


def detect_encoding(raw_bytes: bytes) -> str:
    """
    自动检测字节流的编码。

    顺序：chardet（如有）→ utf-8 → gbk → gb2312 → latin1

    Args:
        raw_bytes: 原始字节数据

    Returns:
        检测到的编码名称，默认为 utf-8
    """
    # 优先 chardet
    if _HAS_CHARDET:
        try:
            result = chardet.detect(raw_bytes)
            encoding = result.get("encoding", "")
            confidence = result.get("confidence", 0)
            if encoding and confidence > 0.7:
                return encoding
        except Exception:
            pass

    # 尝试配置的编码列表
    for enc in ["utf-8", "gbk", "gb2312", "latin1"]:
        try:
            raw_bytes.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue

    return "utf-8"


def decode_response(raw_bytes: bytes, preferred_encoding: str = "utf-8") -> str:
    """
    智能解码响应内容。

    Args:
        raw_bytes: 原始字节数据
        preferred_encoding: 优先使用的编码

    Returns:
        解码后的字符串
    """
    try:
        return raw_bytes.decode(preferred_encoding)
    except UnicodeDecodeError:
        pass

    # 自动检测编码
    detected = detect_encoding(raw_bytes)
    try:
        return raw_bytes.decode(detected)
    except UnicodeDecodeError:
        for enc in ["utf-8", "gbk", "gb2312"]:
            try:
                return raw_bytes.decode(enc, errors="ignore")
            except Exception:
                continue
        return raw_bytes.decode("latin1", errors="ignore")


# =============================================================================
# 6. 便捷封装函数（供 fetchlayer.py 使用）
# =============================================================================

def curl_keeper(url: str, timeout: float = 15.0, source_name: str = "未知") -> FetchResult:
    """
    curl 单次请求封装（简化版，不重试）。

    Args:
        url: 请求URL
        timeout: 超时秒数
        source_name: 数据源名称

    Returns:
        FetchResult: 请求结果
    """
    import subprocess
    import time

    start_time = time.time()

    try:
        cmd = [
            "curl", "-s", "-S", "--noproxy", "*",
            "-m", str(int(timeout)),
            "-H", "Accept: text/html,application/json,*/*",
            "-H", "Accept-Encoding: gzip, deflate",
            "--compressed",
            url
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout + 2,
        )

        elapsed = time.time() - start_time

        if result.returncode == 0:
            content = decode_response(result.stdout)
            return FetchResult(
                success=True,
                content=content,
                encoding="auto",
                status_code=200,
                elapsed=elapsed,
                source=source_name,
            )
        else:
            error_msg = result.stderr.decode("utf-8", errors="ignore") if result.stderr else "curl failed"
            return FetchResult(
                success=False,
                content="",
                encoding="",
                status_code=0,
                elapsed=elapsed,
                error=error_msg,
                source=source_name,
            )

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start_time
        return FetchResult(
            success=False,
            content="",
            encoding="",
            status_code=0,
            elapsed=elapsed,
            error="timeout",
            source=source_name,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        return FetchResult(
            success=False,
            content="",
            encoding="",
            status_code=0,
            elapsed=elapsed,
            error=str(e),
            source=source_name,
        )


def fetch_with_retry(url: str, config: DataSourceConfig) -> FetchResult:
    """
    带指数退避重试的请求。

    Args:
        url: 请求URL
        config: 数据源配置

    Returns:
        FetchResult: 最终请求结果
    """
    timeout = config.base_timeout
    for attempt in range(config.max_retries):
        result = curl_keeper(url, timeout, config.name)
        if result.success:
            return result

        # 不可恢复错误直接返回
        if result.error and any(
            code in result.error for code in ["404", "403", "401", "400"]
        ):
            return result

        # 指数退避
        if attempt < config.max_retries - 1:
            wait_time = timeout * (2 ** attempt)  # 15s → 30s → 60s
            print(f"[RETRY] {config.name} 第{attempt+1}次失败，等待{wait_time}s后重试", file=__import__('sys').stderr)
            __import__('time').sleep(wait_time)
            timeout = min(timeout * 2, 60)  # 上限60秒

    return result


def fetch_multi_async(urls: List[str], configs: List[DataSourceConfig], max_workers: int = 5) -> List[FetchResult]:
    """
    多源并发请求。

    Args:
        urls: URL列表
        configs: 对应的数据源配置列表
        max_workers: 最大并发数

    Returns:
        List[FetchResult]: 各源请求结果
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time

    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_with_retry, url, cfg): (url, cfg)
            for url, cfg in zip(urls, configs)
        }

        for future in as_completed(futures):
            url, cfg = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append(FetchResult(
                    success=False,
                    content="",
                    encoding="",
                    status_code=0,
                    elapsed=0,
                    error=str(e),
                    source=cfg.name,
                ))

    return results


def normalize_percent_multi_source(values: List[Any], source_names: List[str]) -> List[float]:
    """
    多源百分比归一化。

    Args:
        values: 各源的原始值列表
        source_names: 对应的数据源名称列表

    Returns:
        List[float]: 归一化后的百分比列表
    """
    results = []
    for val, src in zip(values, source_names):
        # 为板块数据源添加"板块"标记，确保正确处理
        src_normalized = src
        if "新浪" in src and "板块" not in src:
            src_normalized = src + "板块"
        results.append(normalize_percent(val, src_normalized))
    return results


def build_source_configs() -> Dict[str, DataSourceConfig]:
    """
    构建标准数据源配置（扁平字典）。

    Returns:
        Dict: 按名称 -> 配置 组织，方便 fetchlayer.py 使用
    """
    return {
        "腾讯": DataSourceConfig(name="腾讯", priority=1, base_timeout=15.0, max_retries=3),
        "新浪": DataSourceConfig(name="新浪", priority=2, base_timeout=10.0, max_retries=2),
        "东财": DataSourceConfig(name="东财", priority=3, base_timeout=15.0, max_retries=2),
        "新浪板块": DataSourceConfig(name="新浪板块", priority=1, base_timeout=10.0, max_retries=2),
        "东财板块": DataSourceConfig(name="东财板块", priority=2, base_timeout=15.0, max_retries=2),
        "东财涨停": DataSourceConfig(name="东财涨停", priority=1, base_timeout=15.0, max_retries=2),
        "腾讯港股": DataSourceConfig(name="腾讯港股", priority=1, base_timeout=15.0, max_retries=3),
        "东财港股": DataSourceConfig(name="东财港股", priority=2, base_timeout=15.0, max_retries=2),
    }


# =============================================================================
# 入口自检
# =============================================================================

if __name__ == "__main__":
    print("=== datafoundation.py 自检 ===\n")

    # 1. DataSourceConfig
    print("1. DataSourceConfig:")
    cfg = DataSourceConfig(name="腾讯", priority=1, base_timeout=15.0, max_retries=3)
    print(f"   {cfg}\n")

    # 2. SourceQuality
    print("2. SourceQuality:")
    sq = SourceQuality(
        score=0.85,
        completeness=0.9,
        consistency=0.8,
        reasonableness=0.9,
        issues=["轻微延迟"],
    )
    print(f"   {sq}\n")

    # 3. CurlKeeper (不执行网络请求，只验证可实例化)
    print("3. CurlKeeper:")
    keeper = CurlKeeper(cfg)
    print(f"   实例化成功: {keeper.config.name}\n")

    # 4. QualityScorer
    print("4. QualityScorer:")
    scorer = QualityScorer()
    test_index = {
        "name": "上证指数", "code": "000001",
        "price": 4051.43, "prev_close": 4000.0, "change_pct": 1.28
    }
    sq_index = scorer.score_index_data(test_index, "腾讯")
    print(f"   指数评分: score={sq_index.score:.2f}, issues={sq_index.issues}\n")

    # 5. normalize_percent
    print("5. normalize_percent (×100 问题根治):")
    test_cases = [
        (0.4996, "腾讯"),
        (1.24, "新浪"),
        (1.5, "东财"),
        (55.0, "网易"),
    ]
    for val, src in test_cases:
        result = normalize_percent(val, src)
        print(f"   {src}: {val} → {result:.2f}%\n")

    print("=== 自检完成 ===")