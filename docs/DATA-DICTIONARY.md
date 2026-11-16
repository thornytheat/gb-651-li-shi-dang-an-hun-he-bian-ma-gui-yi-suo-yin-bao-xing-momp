# 数据字典

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `run_id` | string | 否 | 输入摘要派生的稳定运行编号 |
| `source_line` | integer | 否 | 原文件物理行号 |
| `reason_code` | string | 是 | 隔离或冲突的稳定原因码 |
| `raw_value` | string | 是 | 未归一的原始证据 |
| `normalized_value` | string | 是 | 按项目规则得到的值 |
| `rule_version` | string | 否 | 计算规则版本 |
| `file_id` | string | 否 | 原字节 hex + 物理行号派生的稳定文件标识 |
| `blob_hex` | string | 否 | 原始字节十六进制，复核全程逐字保留不改写 |
| `base_version` | integer | 否 | 复核决定所基于的当前版本，乐观并发校验用 |
| `decision.version` | integer | 否 | 复核决定版本号，只追加；v0 为旧批次索引基线 |
| `decision_hash` | string | 否 | 决定内容摘要，相同重试据此幂等去重 |
| `publish_run` | string | 否 | 发布运行编号，由目标摘要派生，中断续跑复用 |

字符串编号逐字保留。金额与计量值用 `Decimal`。时间值带偏移并以 UTC 参与比较。区间统一采用左闭右开。

## 编码复核表（M2 扩展，与 `runs` 表物理隔离）

| 表 | 用途 | 关键约束 |
|---|---|---|
| `review_files` | 待复核文件登记 | `UNIQUE(blob_hex, source_line)`，同字节同行号幂等 |
| `review_decisions` | 人工复核决定，只追加 | `PRIMARY KEY(file_id, version)`、`UNIQUE(file_id, decision_hash)` |
| `review_index` | 分版本索引，v0 为旧批次基线 | `PRIMARY KEY(file_id, version)`，切换冲突整体回滚 |
| `publish_runs` / `publish_items` | 可续跑发布运行与逐项完成标记 | 同 `run_id` 重入不重复生成索引版本 |
