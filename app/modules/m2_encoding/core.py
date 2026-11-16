from __future__ import annotations
import csv
import io

SAMPLE = {"hex": "e6a1a3e58fb7"}

# 候选编码按固定顺序枚举；历史批次（rule_version 2026.11）的排序也依赖这个顺序。
CANDIDATE_ENCODINGS = ("utf-8-sig", "gb18030")


def _score(text: str) -> int:
    return sum(1 for c in text if "一" <= c <= "鿿") * 4 - sum(
        1 for c in text if ord(c) < 32 and c not in "\r\n\t"
    ) * 8


def detect(blob: bytes):
    """历史评分裁决：完整可解码的候选中取分最高，平分时偏向 utf-8-sig。

    旧批次的档号与差异证据必须仍按本函数复现，因此签名与排序口径保持不变。
    """
    if blob.startswith(b"\xef\xbb\xbf"):
        text = blob.decode("utf-8-sig", "strict")
        return {"encoding": "utf-8-sig", "text": text,
                "score": _score(text), "reason": "OK"}
    candidates = []
    for name in CANDIDATE_ENCODINGS:
        try:
            text = blob.decode(name, "strict")
            candidates.append((_score(text), name, text))
        except UnicodeDecodeError:
            pass
    if not candidates:
        return {"encoding": "REJECTED", "text": "", "reason": "DECODE_FAILED"}
    score, name, text = max(candidates, key=lambda x: (x[0], x[1] == "utf-8-sig"))
    return {"encoding": name, "text": text, "score": score, "reason": "OK"}


def candidates(blob: bytes) -> dict:
    """枚举同一字节下所有可完整、严格、无替换字符解读的候选。

    返回每个候选的原文与评分，供人工复核；不在这里改写历史裁决。
    """
    found = []
    for name in CANDIDATE_ENCODINGS:
        try:
            text = blob.decode(name, "strict")
        except UnicodeDecodeError:
            continue
        # 严格解码已保证无替换字符；再显式拒绝 U+FFFD，防止编码别名差异。
        if "�" in text:
            continue
        found.append({"encoding": name, "text": text, "score": _score(text)})
    if not found:
        return {"viable": [], "auto": {"encoding": "REJECTED", "text": "",
                                       "score": None, "reason": "DECODE_FAILED"},
                "ambiguous": False}
    auto = detect(blob)
    top = max(c["score"] for c in found)
    tied = [c["encoding"] for c in found if c["score"] == top]
    return {"viable": found,
            "auto": {"encoding": auto["encoding"], "text": auto["text"],
                     "score": auto["score"], "reason": auto["reason"]},
            # 同一字节存在两种完整解读即进入人工复核；平分时旧评分的偏向裁决尤其危险。
            "ambiguous": len(found) > 1,
            "tied_encodings": tied}


def first_field(text: str) -> str:
    """从一行 CSV 文本中取首列档号；逐字保留，不剥离前导零。"""
    row = next(csv.reader(io.StringIO(text)))
    return str(row[0]) if row else ""


def run(payload):
    raw = bytes.fromhex(payload.get("hex", SAMPLE["hex"]))
    out = detect(raw)
    return {"status": "READY", "input_count": len(raw),
            "valid_count": 1 if out["reason"] == "OK" else 0,
            "quarantine_count": 0 if out["reason"] == "OK" else 1, **out}
