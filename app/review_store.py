from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .domain import DomainError
from .manifest import canonical_digest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_id(blob_hex: str, source_line: int) -> str:
    """同一字节 + 同一物理行号派生稳定文件标识（逐字前导零由调用方保存在 blob 中）。"""
    return "file-" + canonical_digest({"blob": blob_hex.lower(), "line": source_line})[:12]


def decision_id(blob_hex: str, version: int) -> str:
    return "dec-" + canonical_digest({"blob": blob_hex.lower(), "version": version})[:12]


def publish_id(payload: dict) -> str:
    return "pub-" + canonical_digest(payload)[:12]


class ReviewStore:
    """编码复核存储：决定只追加、索引按版本留痕、发布可续跑且不重复出版本。"""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS review_files (
                    file_id       TEXT PRIMARY KEY,
                    blob_hex      TEXT NOT NULL,
                    source_line   INTEGER NOT NULL,
                    current_version INTEGER NOT NULL DEFAULT 0,
                    created_at    TEXT NOT NULL,
                    UNIQUE(blob_hex, source_line)
                );
                CREATE TABLE IF NOT EXISTS review_decisions (
                    file_id     TEXT NOT NULL,
                    version     INTEGER NOT NULL,
                    encoding    TEXT NOT NULL,
                    text        TEXT NOT NULL,
                    score       INTEGER,
                    reason_code TEXT NOT NULL,
                    reason_text TEXT NOT NULL,
                    reviewer    TEXT NOT NULL,
                    base_version INTEGER NOT NULL,
                    created_at  TEXT NOT NULL,
                    decision_hash TEXT NOT NULL,
                    PRIMARY KEY (file_id, version),
                    UNIQUE (file_id, decision_hash)
                );
                CREATE TABLE IF NOT EXISTS review_index (
                    file_id     TEXT NOT NULL,
                    version     INTEGER NOT NULL,
                    encoding    TEXT NOT NULL,
                    text        TEXT NOT NULL,
                    archive_id  TEXT NOT NULL,
                    published_by TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    publish_run TEXT NOT NULL,
                    PRIMARY KEY (file_id, version)
                );
                CREATE TABLE IF NOT EXISTS publish_runs (
                    run_id      TEXT PRIMARY KEY,
                    status      TEXT NOT NULL,
                    digest      TEXT NOT NULL,
                    started_at  TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS publish_items (
                    run_id      TEXT NOT NULL,
                    file_id     TEXT NOT NULL,
                    version     INTEGER NOT NULL,
                    done        INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (run_id, file_id)
                );
                """
            )

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    # ---- 文件登记 -------------------------------------------------------
    def register_file(self, blob_hex: str, source_line: int) -> dict:
        blob_hex = blob_hex.lower()
        fid = file_id(blob_hex, source_line)
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO review_files(file_id, blob_hex, source_line, created_at) "
                "VALUES (?, ?, ?, ?)", (fid, blob_hex, source_line, _now()))
            row = db.execute(
                "SELECT file_id, blob_hex, source_line, current_version FROM review_files WHERE file_id=?",
                (fid,)).fetchone()
        return dict(row)

    def get_file(self, db: sqlite3.Connection, file_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM review_files WHERE file_id=?", (file_id,)).fetchone()
        if row is None:
            raise DomainError("FILE_NOT_FOUND", f"待复核文件不存在: {file_id}")
        return row

    # ---- 复核决定（追加 + 乐观锁）--------------------------------------
    def add_decision(self, file_id: str, encoding: str, text: str, score,
                     reason_code: str, reason_text: str, reviewer: str,
                     base_version: int) -> dict:
        digest = canonical_digest(
            {"encoding": encoding, "text": text, "reason": reason_text,
             "reviewer": reviewer, "base_version": base_version})
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.get_file(db, file_id)
            # 幂等优先：完全相同的复核决定（含 base_version）重试直接返回原版本，
            # 不新生成版本；其余基于旧版本的提交仍按过期拒绝。
            existing = db.execute(
                "SELECT version FROM review_decisions WHERE file_id=? AND decision_hash=?",
                (file_id, digest)).fetchone()
            if existing is not None:
                db.commit()
                return self.decision_detail(file_id, existing["version"])
            if row["current_version"] != base_version:
                db.rollback()
                raise DomainError("STALE_VERSION",
                                  f"提交版本已过期：当前 v{row['current_version']}，"
                                  f"提交基于 v{base_version}")
            version = base_version + 1
            try:
                db.execute(
                    "INSERT INTO review_decisions(file_id, version, encoding, text, score, "
                    "reason_code, reason_text, reviewer, base_version, created_at, decision_hash) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (file_id, version, encoding, text, score, reason_code, reason_text,
                     reviewer, base_version, _now(), digest))
            except sqlite3.IntegrityError:
                db.rollback()
                dup = db.execute(
                    "SELECT version FROM review_decisions WHERE file_id=? AND decision_hash=?",
                    (file_id, digest)).fetchone()
                return self.decision_detail(file_id, dup["version"])
            db.execute("UPDATE review_files SET current_version=? WHERE file_id=?",
                       (version, file_id))
            db.commit()
        return self.decision_detail(file_id, version)

    def decision_detail(self, file_id: str, version: int) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM review_decisions WHERE file_id=? AND version=?",
                (file_id, version)).fetchone()
            if row is None:
                raise DomainError("DECISION_NOT_FOUND",
                                  f"决定版本不存在: {file_id} v{version}")
            return dict(row)

    def history(self, file_id: str) -> dict:
        with self.connect() as db:
            f = self.get_file(db, file_id)
            decisions = [dict(r) for r in db.execute(
                "SELECT version, encoding, text, score, reason_code, reason_text, "
                "reviewer, base_version, created_at FROM review_decisions "
                "WHERE file_id=? ORDER BY version", (file_id,))]
            index_rows = [dict(r) for r in db.execute(
                "SELECT version, encoding, text, archive_id, published_by, published_at, "
                "publish_run FROM review_index WHERE file_id=? ORDER BY version", (file_id,))]
        return {"file": dict(f), "decisions": decisions, "index_versions": index_rows}

    def list_files(self) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT f.*, d.encoding AS latest_encoding, d.text AS latest_text, "
                "d.reason_code AS latest_reason, "
                "(SELECT COUNT(*) FROM review_index i WHERE i.file_id=f.file_id) AS index_versions "
                "FROM review_files f LEFT JOIN review_decisions d "
                "ON d.file_id=f.file_id AND d.version=f.current_version ORDER BY f.source_line, f.file_id")]

    # ---- 发布（原子切换、失败留旧版、中断续跑、重试不重复）--------------
    def ensure_publish_run(self, run_id: str, digest: str) -> str:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO publish_runs(run_id, status, digest, started_at) "
                "VALUES (?, 'RUNNING', ?, ?)", (run_id, digest, _now()))
            row = db.execute("SELECT status FROM publish_runs WHERE run_id=?",
                             (run_id,)).fetchone()
            return row["status"]

    def finish_publish_run(self, run_id: str, status: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE publish_runs SET status=?, finished_at=? WHERE run_id=?",
                (status, _now(), run_id))

    def get_publish_run(self, run_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM publish_runs WHERE run_id=?",
                             (run_id,)).fetchone()
            if row is None:
                raise DomainError("PUBLISH_NOT_FOUND", f"发布运行不存在: {run_id}")
            items = [dict(r) for r in db.execute(
                "SELECT file_id, version, done FROM publish_items WHERE run_id=? ORDER BY file_id",
                (run_id,))]
        out = dict(row)
        out["items"] = items
        return out

    def pending_items(self, run_id: str) -> list[sqlite3.Row]:
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT file_id, version FROM publish_items WHERE run_id=? AND done=0 ORDER BY file_id",
                (run_id,))]

    def plan_publish(self, run_id: str, targets: list[dict]) -> None:
        """登记发布计划；同一 run 重入时保留原计划，不生成新版本。"""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for t in targets:
                db.execute(
                    "INSERT OR IGNORE INTO publish_items(run_id, file_id, version, done) "
                    "VALUES (?, ?, ?, 0)", (run_id, t["file_id"], t["version"]))
            db.commit()

    def switch_index(self, run_id: str, file_id: str, version: int,
                     encoding: str, text: str, archive_id: str, reviewer: str) -> None:
        """单文件索引切换：插入索引版本与完成标记在同一事务内原子提交。

        已存在同版本索引行时，只有内容与本次已存决定完全一致才按幂等成功；
        内容冲突则整体回滚并报错，旧索引版本原样保留。
        """
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT encoding, text, archive_id FROM review_index "
                "WHERE file_id=? AND version=?", (file_id, version)).fetchone()
            if row is not None:
                if (row["encoding"], row["text"], row["archive_id"]) != \
                        (encoding, text, archive_id):
                    db.rollback()
                    raise DomainError("INDEX_SWITCH_FAILED",
                                      f"索引切换失败，已保留旧版: {file_id} v{version} "
                                      "已存在内容不一致的索引行")
                db.execute("UPDATE publish_items SET done=1 WHERE run_id=? AND file_id=?",
                           (run_id, file_id))
                db.commit()
                return
            try:
                db.execute(
                    "INSERT INTO review_index(file_id, version, encoding, text, archive_id, "
                    "published_by, published_at, publish_run) VALUES (?,?,?,?,?,?,?,?)",
                    (file_id, version, encoding, text, archive_id, reviewer, _now(), run_id))
                db.execute("UPDATE publish_items SET done=1 WHERE run_id=? AND file_id=?",
                           (run_id, file_id))
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
                raise DomainError("INDEX_SWITCH_FAILED",
                                  f"索引切换失败，已保留旧版: {file_id}")

    # ---- 分版本对账 -----------------------------------------------------
    def reconcile(self) -> dict:
        with self.connect() as db:
            files = [dict(r) for r in db.execute("SELECT * FROM review_files ORDER BY file_id")]
            rows = []
            for f in files:
                idx = [dict(r) for r in db.execute(
                    "SELECT * FROM review_index WHERE file_id=? ORDER BY version",
                    (f["file_id"],))]
                latest = db.execute(
                    "SELECT * FROM review_decisions WHERE file_id=? ORDER BY version DESC LIMIT 1",
                    (f["file_id"],)).fetchone()
                rows.append({"file_id": f["file_id"], "source_line": f["source_line"],
                             "blob_hex": f["blob_hex"], "current_version": f["current_version"],
                             "index_versions": idx,
                             "latest_decision": dict(latest) if latest else None})
        return {"files": rows, "file_count": len(rows),
                "index_version_count": sum(len(r["index_versions"]) for r in rows)}
