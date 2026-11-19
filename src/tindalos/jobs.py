"""Bounded execution with durable progress; one runtime per application process.

The SQLite transaction commits a generated campaign and the terminal job event
together. Disconnecting a viewer does not cancel work. Explicit cancellation is
cooperative: an in-flight model request can finish, but its result is not adopted.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from typing import Callable

from tindalos import history

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class CapacityError(RuntimeError):
    pass


class JobRuntime:
    def __init__(self, generate: Callable, *, workers: int = 2, capacity: int = 16, max_duration: float = 600):
        if workers < 1 or capacity < workers:
            raise ValueError("capacity must be >= workers >= 1")
        from tindalos.lease import acquire_lease
        from pathlib import Path
        self._lease = acquire_lease(Path(str(history.db_path()) + '.runtime.lock'))
        self.max_duration = max_duration
        self.workers, self.capacity = workers, capacity
        self.generate = generate
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tindalos-job")
        self.slots = threading.BoundedSemaphore(capacity)
        self.lock = threading.Lock()
        self.closed = False
        try:
            history.init_db()
            self._recover()
        except BaseException:
            self.pool.shutdown(wait=False)
            self._lease.close()
            raise

    def _recover(self):
        with closing(history._connect()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, request_hash TEXT NOT NULL,
                    request_json TEXT NOT NULL, status TEXT NOT NULL, campaign_id TEXT,
                    revision INTEGER, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_events (
                    job_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(job_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            """)
            conn.execute('BEGIN IMMEDIATE')
            for row in conn.execute("SELECT id FROM jobs WHERE status IN ('queued','running')").fetchall():
                self._terminal(conn, row["id"], "interrupted", error="process restarted; submit a new job")
            conn.commit()

    @staticmethod
    def _append(conn, job_id: str, payload: dict):
        seq = conn.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM job_events WHERE job_id=?",
                           (job_id,)).fetchone()[0]
        conn.execute("INSERT INTO job_events VALUES (?,?,?)",
                     (job_id, seq, json.dumps(payload, ensure_ascii=False)))

    def _terminal(self, conn, job_id, status, *, error=None, campaign=None, revision=None):
        conn.execute("UPDATE jobs SET status=?,error=?,campaign_id=?,revision=?,updated_at=? WHERE id=?",
                     (status,error,campaign.get("id") if campaign else None,revision,time.time(),job_id))
        payload = {"done": True, "campaign": campaign, "job_id": job_id, "status": status,
                   "revision": revision}
        if error:
            self._append(conn, job_id, {"error": error, "job_id": job_id})
        self._append(conn, job_id, payload)

    def lookup(self, key: str, identity: dict) -> dict | None:
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with closing(history._connect()) as conn:
            old = conn.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
            if old and old["request_hash"] != digest:
                raise history.RevisionConflict("idempotency key reused with different input")
            return self._payload(old) if old else None

    def submit(self, request: dict, *, key: str | None = None, identity: dict | None = None) -> dict:
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(json.dumps(identity if identity is not None else request,
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        # The admission lock covers key lookup and insertion; requests racing
        # with the same key get the same durable job, not two model executions.
        with self.lock:
            if self.closed:
                raise CapacityError("runtime is shutting down")
            with closing(history._connect()) as conn:
                if key:
                    old = conn.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
                    if old:
                        if old["request_hash"] != digest:
                            raise history.RevisionConflict("idempotency key reused with different input")
                        return self._payload(old)
                if not self.slots.acquire(blocking=False):
                    raise CapacityError("generation queue is full")
                jid = "job-" + uuid.uuid4().hex
                try:
                    now = time.time()
                    conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (jid,key,digest,encoded,"queued",None,None,None,now,now))
                    self._append(conn, jid, {"stage":"queued", "message":"任务已排队", "job_id":jid})
                    conn.commit()
                    self.pool.submit(self._run, jid, request)
                except BaseException:
                    self.slots.release()
                    raise
            return self.get(jid)

    @staticmethod
    def _payload(row) -> dict:
        return {k: row[k] for k in ("id","status","campaign_id","revision","error","created_at","updated_at")}

    def get(self, job_id: str) -> dict | None:
        with closing(history._connect()) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._payload(row) if row else None

    def events(self, job_id: str, after: int = 0) -> list[dict]:
        with closing(history._connect()) as conn:
            return [{"sequence": r["sequence"], "payload":json.loads(r["payload"])} for r in conn.execute(
                "SELECT sequence,payload FROM job_events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT 128",
                (job_id,after))]

    def cancel(self, job_id: str) -> dict | None:
        with closing(history._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return None
            if row["status"] not in TERMINAL:
                self._terminal(conn, job_id, "cancelled", error="任务已取消")
            conn.commit()
        return self.get(job_id)

    def _run(self, job_id: str, request: dict):
        deadline = time.monotonic() + self.max_duration
        try:
            with closing(history._connect()) as conn:
                cur = conn.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=? AND status='queued'",
                                   (time.time(),job_id))
                conn.commit()
                if not cur.rowcount:
                    return

            def emit(stage, message):
                with closing(history._connect()) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
                    if row[0] != "running":
                        return False
                    if time.monotonic() > deadline:
                        self._terminal(conn, job_id, 'failed', error='generation exceeded its time budget')
                        conn.commit()
                        return False
                    self._append(conn, job_id, {"stage":stage,"message":message,"job_id":job_id})
                    conn.commit()
                    return True

            campaign = self.generate(request, emit)
            if campaign is not None:
                from tindalos.models import Campaign
                campaign = Campaign.model_validate(campaign).model_dump(mode="json")
            with closing(history._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                status = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                if status != "running":
                    return
                if time.monotonic() > deadline:
                    raise TimeoutError('generation exceeded its time budget')
                if campaign is None:
                    self._terminal(conn, job_id, "failed", error="generation did not return a campaign")
                else:
                    rec = history._write_campaign(conn, campaign["id"], campaign.get("title", ""),
                        campaign.get("rules", "COC7"), campaign,
                        module_id=request.get("module_id"), expected_revision=request.get("expected_revision"),
                        reason="continue" if request.get("campaign_id") else "generated")
                    self._terminal(conn, job_id, "completed", campaign=campaign, revision=rec["revision"])
                conn.commit()
        except Exception as exc:
            with closing(history._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row and row[0] not in TERMINAL:
                    self._terminal(conn, job_id, "failed", error=str(exc))
                conn.commit()
        finally:
            self.slots.release()

    def close(self):
        with self.lock:
            self.closed = True
        with closing(history._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in conn.execute("SELECT id FROM jobs WHERE status IN ('queued','running')").fetchall():
                self._terminal(conn, row[0], "interrupted", error="application shutting down")
            conn.commit()
        self.pool.shutdown(wait=True, cancel_futures=False)
        self._lease.close()
