# 报告验证元数据（通用规则）

此文件定义所有报告类型的通用验证规则，由 validate_report.py 动态读取。

---

## 禁止模式（所有报告类型通用）

<!--
FORBIDDEN:
由AI深度分析后补充
待补充
暂无数据
[待填充]
...补充
[待更新]
数据获取失败
FORBIDDEN:END
-->

这些模式在任何报告中出现都将导致验证失败。

---

## 报告类型检测规则

<!--
REPORT_TYPE_RULES:
日报标题模式: # YYYYMMDD 每日复盘 或 # 每日复盘
周报标题模式: # YYYYMMDD 周度复盘报告 或 # 周度复盘报告
月报标题模式: # YYYYMMDD 月度复盘报告 或 # 月度复盘报告
REPORT_TYPE_RULES:END
-->

---

## 数据依赖映射

<!--
DATA_DEPS:
indices → 一、市场全景概览
sectors → 三、热门板块深度解析
zt_pool → 四、核心个股追踪
watchlist_a → 五、自选股跟踪
watchlist_hk → 五、自选股跟踪
tavily_results → 二、财经要闻
DATA_DEPS:END
-->

验证脚本可用此映射检查数据来源是否覆盖关键章节。