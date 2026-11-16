REASONS = {
    "BAD_COLUMNS": "字段数量不符", "EMPTY_VALUE": "必要值为空",
    "DUPLICATE_CONFLICT": "同一业务键取值冲突", "TRUNCATED": "尾记录不完整",
    "INVALID_VALUE": "值不符合定义", "STALE_VERSION": "提交版本已过期",
    "REVIEW_CORRECTED": "人工复核更正编码裁决",
    "REVIEW_CONFIRMED": "人工复核确认自动裁决",
    "DECODE_FAILED": "候选编码均无法完整解码",
    "SCORE_AMBIGUOUS": "多编码完整可解码且评分并列，自动裁决不可信",
    "INDEX_SWITCH_FAILED": "索引切换失败，旧版已保留",
}

def reason(code: str) -> str:
    return REASONS.get(code, "未登记原因")
