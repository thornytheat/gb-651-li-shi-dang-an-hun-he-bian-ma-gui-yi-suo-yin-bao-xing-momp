import threading

import pytest

from app.domain import DomainError
from app.modules.m2_encoding import core, review
from app.modules.m2_encoding.core import candidates, detect, first_field
from app.review_store import ReviewStore

# 同一字节可被 utf-8-sig 与 gb18030 完整解读，且平分；历史评分偏向 utf-8-sig。
AMBIGUOUS_HEX = "e6a1a3e58fb7"   # UTF-8「档号」；GB18030 解为「妗ｅ彿」
CATALOG_HEX = "e79baee5bd95"     # UTF-8「目录」；同样双解码、平分
GB_HEX = "b5b5bac5"              # GB18030「档号」；UTF-8 无法解码


@pytest.fixture
def store(tmp_path):
    return ReviewStore(str(tmp_path / "review.db"))


@pytest.fixture
def seeded(store):
    review.seed(store)
    return store


def _file_id(store, blob_hex, line):
    return store.register_file(blob_hex, line)["file_id"]


def _baseline(store, blob_hex=AMBIGUOUS_HEX, line=2, encoding="utf-8-sig"):
    """登记文件并按历史裁决写入 v0 基线，模拟旧批次已发布的索引版本。"""
    fid = _file_id(store, blob_hex, line)
    blob = bytes.fromhex(blob_hex)
    text = blob.decode(encoding, "strict")
    with store.connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO review_index(file_id, version, encoding, text, archive_id, "
            "published_by, published_at, publish_run) VALUES (?,?,?,?,?,?,?,?)",
            (fid, 0, encoding, text, core.first_field(text), "legacy-batch",
             "2026-11-01T00:00:00+00:00", "pub-baseline"))
    return fid


def _correct(store, fid, encoding="gb18030", base_version=0, reviewer="alice",
             reason_text="旧批次误读，按 GB18030 复核"):
    return review.review(store, {
        "file_id": fid, "encoding": encoding, "base_version": base_version,
        "reason_code": "REVIEW_CORRECTED", "reason_text": reason_text,
        "reviewer": reviewer})


# ---- 核心解码：旧裁决口径不变，候选全部可见 ----

def test_legacy_detect_still_prefers_utf8_on_tie():
    out = detect(bytes.fromhex(AMBIGUOUS_HEX))
    assert out["encoding"] == "utf-8-sig"          # 旧批次复现口径保持
    assert out["score"] == 8


def test_dual_full_decodes_are_both_listed():
    cand = candidates(bytes.fromhex(AMBIGUOUS_HEX))
    encs = {c["encoding"] for c in cand["viable"]}
    assert encs == {"utf-8-sig", "gb18030"}
    assert cand["ambiguous"] is True
    assert all("�" not in c["text"] for c in cand["viable"])


def test_gb_bytes_only_decode_as_gb():
    cand = candidates(bytes.fromhex(GB_HEX))
    assert [c["encoding"] for c in cand["viable"]] == ["gb18030"]
    assert cand["viable"][0]["text"] == "档号"
    assert cand["ambiguous"] is False


def test_undecodable_bytes_have_no_viable_candidate():
    cand = candidates(b"\x81")
    assert cand["viable"] == []
    assert cand["auto"]["reason"] == "DECODE_FAILED"


def test_first_field_keeps_leading_zeros():
    assert first_field("000123,07,年度 汇编") == "000123"


# ---- 登记与基线：旧批次证据按原解码冻结 ----

def test_seed_builds_v0_baseline_with_legacy_pick(seeded):
    rec = review.reconcile(seeded)
    target = next(f for f in rec["files"] if f["blob_hex"] == AMBIGUOUS_HEX)
    assert target["source_line"] == 2                  # 物理行号保留
    assert target["blob_hex"] == AMBIGUOUS_HEX         # 原字节保留
    assert target["baseline"]["version"] == 0
    assert target["baseline"]["encoding"] == "utf-8-sig"  # 旧评分选择被冻结
    assert target["baseline"]["archive_id"] == "档号"


def test_register_is_idempotent_on_same_bytes_and_line(store):
    a = store.register_file(GB_HEX, 7)
    b = store.register_file(GB_HEX, 7)
    assert a["file_id"] == b["file_id"]


def test_same_bytes_different_lines_are_distinct_files(store):
    assert store.register_file(GB_HEX, 1)["file_id"] != \
           store.register_file(GB_HEX, 2)["file_id"]


# ---- 复核决定：理由必填、严格解码、版本只追加 ----

def test_review_persists_versioned_decision_with_reason(store):
    fid = _file_id(store, AMBIGUOUS_HEX, 2)
    out = _correct(store, fid)
    assert out["saved_decision"]["version"] == 1
    assert out["saved_decision"]["text"] == "妗ｅ彿"
    history = store.history(fid)
    assert [d["version"] for d in history["decisions"]] == [1]
    assert history["decisions"][0]["reason_text"] == "旧批次误读，按 GB18030 复核"
    assert history["decisions"][0]["base_version"] == 0


def test_review_requires_reason_text(store):
    fid = _file_id(store, AMBIGUOUS_HEX, 2)
    with pytest.raises(DomainError) as exc:
        review.review(store, {"file_id": fid, "encoding": "gb18030",
                              "base_version": 0, "reason_text": "  "})
    assert exc.value.code == "EMPTY_VALUE"


def test_review_rejects_non_decodable_choice(store):
    fid = _file_id(store, GB_HEX, 7)
    with pytest.raises(DomainError) as exc:
        review.review(store, {"file_id": fid, "encoding": "utf-8-sig",
                              "base_version": 0, "reason_text": "尝试 UTF-8"})
    assert exc.value.code == "DECODE_FAILED"


def test_stale_base_version_is_rejected(store):
    fid = _file_id(store, AMBIGUOUS_HEX, 2)
    _correct(store, fid)
    with pytest.raises(DomainError) as exc:
        review.review(store, {"file_id": fid, "encoding": "utf-8-sig",
                              "base_version": 0, "reason_text": "基于旧版本的并发决定",
                              "reviewer": "bob"})
    assert exc.value.code == "STALE_VERSION"
    assert store.history(fid)["file"]["current_version"] == 1  # 过期决定未产生新版本


def test_concurrent_review_only_one_wins(store):
    fid = _file_id(store, AMBIGUOUS_HEX, 2)
    errors = []

    def worker(encoding, reviewer):
        try:
            _correct(store, fid, encoding=encoding, reviewer=reviewer,
                     reason_text=reviewer)
        except DomainError as exc:
            errors.append(exc.code)

    t1 = threading.Thread(target=worker, args=("gb18030", "alice"))
    t2 = threading.Thread(target=worker, args=("utf-8-sig", "bob"))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert errors == ["STALE_VERSION"]
    assert store.history(fid)["file"]["current_version"] == 1


def test_identical_retry_does_not_create_new_version(store):
    fid = _file_id(store, AMBIGUOUS_HEX, 2)
    payload = {"file_id": fid, "encoding": "gb18030", "base_version": 0,
               "reason_code": "REVIEW_CORRECTED", "reason_text": "同一决定重试",
               "reviewer": "alice"}
    first = review.review(store, dict(payload))
    second = review.review(store, dict(payload))  # 同内容重试命中去重
    assert first["saved_decision"]["version"] == second["saved_decision"]["version"] == 1
    assert len(store.history(fid)["decisions"]) == 1


def test_second_review_chains_on_latest_base(store):
    fid = _file_id(store, AMBIGUOUS_HEX, 2)
    _correct(store, fid, encoding="gb18030")
    again = _correct(store, fid, encoding="utf-8-sig", base_version=1,
                     reason_text="二次复核改回")
    assert again["saved_decision"]["version"] == 2
    assert [d["version"] for d in store.history(fid)["decisions"]] == [1, 2]


# ---- 发布：原子切换、失败留旧版、续跑、重试不重复 ----

def test_publish_switches_index_and_keeps_baseline(store):
    fid = _baseline(store)
    _correct(store, fid)
    run = review.publish(store)
    assert run["status"] == "DONE"
    rec = next(f for f in review.reconcile(store)["files"] if f["file_id"] == fid)
    versions = rec["index_versions"]
    assert [v["version"] for v in versions] == [0, 1]
    assert versions[0]["encoding"] == "utf-8-sig"          # 旧版仍可复现
    assert versions[1]["encoding"] == "gb18030"            # 当前采用新解码
    assert rec["diff"]["changed_archive_id"] is True


def test_publish_is_idempotent_same_run_no_duplicate_versions(store):
    fid = _baseline(store)
    _correct(store, fid)
    first = review.publish(store)
    second = review.publish(store)
    assert first["run_id"] == second["run_id"]
    assert second["status"] == "DONE"
    assert review.reconcile(store)["index_version_count"] == 2  # 仅 v0、v1


def test_interrupted_publish_resumes_from_stored_decisions(store, monkeypatch):
    fid = _baseline(store)
    _correct(store, fid)
    calls = {"n": 0}
    original = review._switch_one

    def flaky(rs, run, file_id, version):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("发布中断")
        return original(rs, run, file_id, version)

    monkeypatch.setattr(review, "_switch_one", flaky)
    first = review.publish(store)
    assert first["status"] == "FAILED"
    monkeypatch.setattr(review, "_switch_one", original)
    second = review.publish(store)  # 同 run_id 续跑
    assert second["run_id"] == first["run_id"]
    assert second["status"] == "DONE"
    assert [v["version"] for v in store.history(fid)["index_versions"]] == [0, 1]


def test_failed_switch_keeps_old_index(store):
    """存储层冲突：目标 (file_id, version) 已存在内容不一致的索引行，
    切换事务整体回滚，旧索引版本保留，发布标记失败。"""
    fid = _baseline(store)
    _correct(store, fid)
    with store.connect() as db:
        db.execute(
            "INSERT INTO review_index(file_id, version, encoding, text, archive_id, "
            "published_by, published_at, publish_run) VALUES (?,?,?,?,?,?,?,?)",
            (fid, 1, "gb18030", "占位内容", "占位档号", "blocker", "t0", "pub-block"))
    run = review.publish(store)
    assert run["status"] == "FAILED"
    assert run["failures"][0]["file_id"] == fid
    versions = store.history(fid)["index_versions"]
    # 旧版 v0 原样保留；冲突的预占 v1 不属于本次发布运行，未被采纳
    assert versions[0]["version"] == 0
    assert versions[0]["encoding"] == "utf-8-sig"
    assert all(v["publish_run"] != run["run_id"] for v in versions)
    assert run["items"][0]["done"] == 0


# ---- 对账：新旧结果分版本 ----

def test_reconcile_reports_old_vs_current(seeded):
    fid = next(f["file_id"] for f in seeded.list_files()
               if f["blob_hex"] == AMBIGUOUS_HEX)
    _correct(seeded, fid)
    review.publish(seeded)
    rec = next(f for f in review.reconcile(seeded)["files"] if f["file_id"] == fid)
    assert rec["baseline"]["archive_id"] != rec["current"]["archive_id"]
    assert review.reconcile(seeded)["reason"] == "DIFF"


def test_reconcile_ok_when_nothing_changed(seeded):
    assert review.reconcile(seeded)["reason"] == "OK"


def test_publish_targets_only_decided_files(seeded):
    # 仅更正第一个文件；发布不应触碰未复核文件的索引
    first = seeded.list_files()[0]
    _correct(seeded, first["file_id"])
    run = review.publish(seeded)
    assert len(run["items"]) == 1
    untouched = next(f for f in review.reconcile(seeded)["files"]
                     if f["file_id"] != first["file_id"])
    assert [v["version"] for v in untouched["index_versions"]] == [0]
