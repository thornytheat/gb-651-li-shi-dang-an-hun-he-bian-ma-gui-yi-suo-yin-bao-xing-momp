"""M2 编码复核编排：在不改变历史评分裁决的前提下，支持按文件人工复核、
分版本留痕、可续跑的索引切换发布与新旧版本对账。"""
from __future__ import annotations

from ...domain import DomainError
from ...review_store import ReviewStore, publish_id
from . import core

REVIEW_REASONS = {"REVIEW_CORRECTED", "REVIEW_CONFIRMED"}


# 演示用夹具：两个文件的同一字节都能被两种编码完整解读，
# 历史评分在平分时偏向 utf-8-sig，其中 GB18030 才是业务正确解读，自动裁决选错。
SEED_FILES = [
    {"name": "archive_dirty.csv:L2", "blob_hex": "e6a1a3e58fb7", "source_line": 2,
     "baseline_encoding": "utf-8-sig"},
    {"name": "legacy_batch.csv:L7", "blob_hex": "e79baee5bd95", "source_line": 7,
     "baseline_encoding": "utf-8-sig"},
]


def _strict_decode(blob: bytes, encoding: str) -> str:
    if encoding not in core.CANDIDATE_ENCODINGS:
        raise _domain("INVALID_VALUE", f"不支持的候选编码: {encoding}")
    try:
        text = blob.decode(encoding, "strict")
    except UnicodeDecodeError:
        raise _domain("DECODE_FAILED", f"字节无法以 {encoding} 完整解码")
    if "�" in text:
        raise _domain("DECODE_FAILED", f"{encoding} 解码出现替换字符")
    return text


def _domain(code: str, message: str) -> DomainError:
    return DomainError(code, message)


def seed(store: ReviewStore) -> dict:
    """登记演示文件，并以历史评分裁决建立索引基线（v0，旧批次证据）。"""
    seeded = []
    for item in SEED_FILES:
        f = store.register_file(item["blob_hex"], item["source_line"])
        blob = bytes.fromhex(item["blob_hex"])
        auto = core.detect(blob)
        _baseline_index(store, f["file_id"], blob, item["baseline_encoding"], auto)
        seeded.append({"file_id": f["file_id"], "blob_hex": item["blob_hex"],
                       "source_line": item["source_line"],
                       "auto_encoding": auto["encoding"],
                       "auto_text": auto["text"]})
    return {"status": "SEEDED", "files": seeded}


def _baseline_index(store: ReviewStore, fid: str, blob: bytes,
                    encoding: str, auto: dict) -> None:
    """基线只写索引历史（v0），不进决定表；决定表从人工复核 v1 开始。"""
    if encoding == auto["encoding"]:
        text = auto["text"]
    else:
        text = blob.decode(encoding, "strict")
    with store.connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO review_index(file_id, version, encoding, text, archive_id, "
            "published_by, published_at, publish_run) VALUES (?,?,?,?,?,?,?,?)",
            (fid, 0, encoding, text, core.first_field(text), "legacy-batch",
             "2026-11-01T00:00:00+00:00", "pub-baseline"))


def register(store: ReviewStore, payload: dict) -> dict:
    blob_hex = payload.get("blob_hex") or payload.get("hex")
    if not blob_hex:
        raise _domain("EMPTY_VALUE", "缺少 blob_hex（原始字节十六进制）")
    try:
        bytes.fromhex(blob_hex)
    except ValueError:
        raise _domain("INVALID_VALUE", "blob_hex 不是合法十六进制")
    source_line = int(payload.get("source_line", 0))
    f = store.register_file(blob_hex, source_line)
    return probe(store, f["file_id"])


def probe(store: ReviewStore, fid: str) -> dict:
    """返回同一字节下全部完整候选与历史自动裁决，供页面对照，不写任何决定。"""
    with store.connect() as db:
        f = store.get_file(db, fid)
    blob = bytes.fromhex(f["blob_hex"])
    cand = core.candidates(blob)
    history = store.history(fid)
    return {"status": "READY", "file": history["file"], "candidates": cand["viable"],
            "auto": cand["auto"], "ambiguous": cand["ambiguous"],
            "tied_encodings": cand["tied_encodings"],
            "history": history["decisions"], "index_versions": history["index_versions"]}


def review(store: ReviewStore, payload: dict) -> dict:
    fid = payload.get("file_id")
    encoding = payload.get("encoding")
    reason_code = payload.get("reason_code", "REVIEW_CORRECTED")
    reason_text = (payload.get("reason_text") or "").strip()
    reviewer = (payload.get("reviewer") or "reviewer").strip()
    base_version = payload.get("base_version")
    if not reason_text:
        raise _domain("EMPTY_VALUE", "复核必须记录理由（reason_text）")
    if reason_code not in REVIEW_REASONS:
        raise _domain("INVALID_VALUE", f"复核原因码须为 {sorted(REVIEW_REASONS)}")
    if base_version is None:
        raise _domain("EMPTY_VALUE", "缺少 base_version（乐观并发版本）")
    base_version = int(base_version)
    with store.connect() as db:
        f = store.get_file(db, fid)
    blob = bytes.fromhex(f["blob_hex"])
    text = _strict_decode(blob, encoding)  # 原字节完整、严格、无替换字符才可保存
    auto = core.detect(blob)
    score = next((c["score"] for c in core.candidates(blob)["viable"]
                  if c["encoding"] == encoding), None)
    saved = store.add_decision(fid, encoding, text, score, reason_code, reason_text,
                               reviewer, base_version)
    out = probe(store, fid)
    out["saved_decision"] = saved
    out["auto_text"] = auto["text"]
    return out


def publish(store: ReviewStore, payload: dict | None = None) -> dict:
    """按已存决定切换索引。同输入摘要派生同一 run_id：中断续跑、重试不重复出版本。"""
    payload = payload or {}
    only = set(payload.get("file_ids") or [])
    targets = _publish_targets(store, only)
    if not targets:
        raise _domain("EMPTY_VALUE", "没有待发布的复核决定")
    run = publish_id({"targets": [(t["file_id"], t["version"]) for t in targets]})
    status = store.ensure_publish_run(run, run)
    store.plan_publish(run, targets)
    failures = []
    if status != "DONE":
        for item in store.pending_items(run):
            try:
                _switch_one(store, run, item["file_id"], item["version"])
            except Exception as exc:  # 单文件失败不污染其他文件；旧索引版本保留
                failures.append({"file_id": item["file_id"], "error": str(exc)})
        store.finish_publish_run(run, "FAILED" if failures else "DONE")
    result = store.get_publish_run(run)
    result["failures"] = failures
    return result


def _switch_one(store: ReviewStore, run: str, fid: str, version: int) -> None:
    decision = store.decision_detail(fid, version)
    # 档号按解码后文本逐字保留，前导零不做数值化。
    archive_id = core.first_field(decision["text"])
    store.switch_index(run, fid, version, decision["encoding"], decision["text"],
                       archive_id, decision["reviewer"])


def _publish_targets(store: ReviewStore, only: set[str]) -> list[dict]:
    targets = []
    for f in store.list_files():
        if only and f["file_id"] not in only:
            continue
        if f["current_version"] and f["current_version"] > 0 and f["latest_encoding"]:
            targets.append({"file_id": f["file_id"], "version": f["current_version"]})
    return targets


def reconcile(store: ReviewStore) -> dict:
    """逐文件对照历史基线（旧解码）与当前索引（新解码），输出分版本差异证据。"""
    data = store.reconcile()
    rows = []
    for item in data["files"]:
        versions = item["index_versions"]
        baseline = next((v for v in versions if v["version"] == 0), None)
        current = versions[-1] if versions else None
        diff = None
        if baseline and current and current["version"] > 0:
            diff = {"changed_encoding": baseline["encoding"] != current["encoding"],
                    "changed_text": baseline["text"] != current["text"],
                    "changed_archive_id": baseline["archive_id"] != current["archive_id"],
                    "old_archive_id": baseline["archive_id"],
                    "new_archive_id": current["archive_id"]}
        rows.append({**item, "baseline": baseline, "current": current, "diff": diff})
    return {"status": "READY", "files": rows, "file_count": len(rows),
            "index_version_count": data["index_version_count"],
            "reason": "DIFF" if any(r["diff"] and r["diff"]["changed_archive_id"]
                                    for r in rows) else "OK"}
