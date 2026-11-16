# 深度登记 历史档案混合编码归一与索引保形

        - 规模：中型
        - 核心规则：候选解码必须完整且无替换字符；档号、盒号与超大流水号始终按字符串逐字保留。
        - 使用者与工作流：导入随仓样例，逐模块复核，最后导出单一证据清单。

        ## 功能模块

        | 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
        |---|---|---|---|---|
        | M1 文件采信 | 运行样例并复核证据 | `/api/intake/run` / `/intake` | `app/modules/m1_intake/core.py` `app/modules/m1_intake/service.py` | `tests/test_m1_intake.py` |
| M2 编码判定 | 运行样例并复核证据 | `/api/encoding/detect` / `/encoding` | `app/modules/m2_encoding/core.py` `app/modules/m2_encoding/service.py` | `tests/test_m2_encoding.py` |
| M2 编码复核 | 按文件复核编码、留理由并发布索引 | `/api/encoding/review/*` / `/encoding` | `app/modules/m2_encoding/review.py` `app/review_store.py` | `tests/test_m2_review.py` |
| M3 字段保形 | 运行样例并复核证据 | `/api/fields/normalize` / `/fields` | `app/modules/m3_fields/core.py` `app/modules/m3_fields/service.py` | `tests/test_m3_fields.py` |
| M4 内容归一 | 运行样例并复核证据 | `/api/content/normalize` / `/content` | `app/modules/m4_content/core.py` `app/modules/m4_content/service.py` | `tests/test_m4_content.py` |
| M5 索引对账 | 运行样例并复核证据 | `/api/reconcile` / `/reconcile` | `app/modules/m5_reconcile/core.py` `app/modules/m5_reconcile/service.py` | `tests/test_m5_reconcile.py` |
| M6 批次导出 | 运行样例并复核证据 | `/api/export/evidence.csv` / `/runs` | `app/modules/m6_runs/core.py` `app/modules/m6_runs/service.py` | `tests/test_m6_runs.py` |

        ## 陷阱登记

        | TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
        |---|---|---|---|---|---|
        | TRAP-01 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_intake/core.py` | `tests/test_m1_intake.py::test_trap_1` |
| TRAP-02 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_encoding/core.py` | `tests/test_m2_encoding.py::test_trap_1` |
| TRAP-03 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_fields/core.py` | `tests/test_m3_fields.py::test_trap_1` |
| TRAP-04 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_content/core.py` | `tests/test_m4_content.py::test_trap_1` |
| TRAP-05 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m5_reconcile/core.py` | `tests/test_m5_reconcile.py::test_trap_1` |
| TRAP-07 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m6_runs/core.py` | `tests/test_m6_runs.py::test_trap_1` |
| TRAP-08 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_intake/core.py` | `tests/test_m1_intake.py::test_trap_2` |
| TRAP-09 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_encoding/core.py` | `tests/test_m2_encoding.py::test_trap_2` |
| TRAP-20 | 同字节双编码完整可解码且评分并列，自动裁决选错 | 直接覆盖旧值或冻结旧值 | 候选全量枚举，人工按文件复核，决定只追加，旧解码冻结为索引 v0 | `app/modules/m2_encoding/review.py` | `tests/test_m2_review.py::test_dual_full_decodes_are_both_listed` |
| TRAP-21 | 两人并发更正产生覆盖更新 | 后写静默覆盖先写 | `base_version` 乐观锁，过期决定返回 422 `STALE_VERSION` | `app/review_store.py` | `tests/test_m2_review.py::test_concurrent_review_only_one_wins` |
| TRAP-22 | 发布中断产生半成品索引或重复版本 | 整体重跑重复出版本 | 目标摘要派生稳定 `run_id`，逐项原子提交，按已存决定续跑且幂等 | `app/review_store.py` | `tests/test_m2_review.py::test_interrupted_publish_resumes_from_stored_decisions` |
| TRAP-23 | 索引切换失败污染当前索引 | 先删旧版再写新版 | 同事务插入新版本，冲突回滚，旧索引版本保留 | `app/review_store.py` | `tests/test_m2_review.py::test_failed_switch_keeps_old_index` |
| TRAP-16 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_fields/core.py` | `tests/test_m3_fields.py::test_trap_2` |
| TRAP-18 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_content/core.py` | `tests/test_m4_content.py::test_trap_2` |

        ## 对账规则

        - 输入记录数 = 有效记录数 + 隔离记录数 + 空记录数。
        - 核心数值或事件数在转换、判定和导出之间保持守恒。
        - 同一输入摘要重跑复用原运行编号，不增加结果行。
        - 编码复核的索引按 `(file_id, version)` 留痕：v0 是旧批次按原解码冻结的基线，人工复核从 v1 起只追加；对账逐文件比较基线与当前版本的编码、文本与档号差异。
        - 发布运行编号由目标 `(file_id, version)` 摘要派生；中断续跑与重试复用同一编号，不重复生成索引版本；单文件切换失败只回滚该文件，旧版索引保留。

        ## 已知不覆盖

        - 生产系统连接、身份认证和外部密钥不在演示范围内。
