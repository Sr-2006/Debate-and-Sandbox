"""SQLite-backed persistent deduplication and processing store for cross-laptop transport events."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Iterator, List

from transport.contracts import EventStatus, ProcessingStatus


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DedupStore:
    """Manages transactional state, deduplication tracking, and incident processing for transport events."""

    def __init__(self, db_path: str = "runtime/transport.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS received_events (
                    event_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    input_path TEXT,
                    last_error TEXT
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_incident_hash
                ON received_events (incident_id, payload_hash);
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS published_results (
                    event_id TEXT PRIMARY KEY,
                    parent_event_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    final_outcome TEXT NOT NULL,
                    report_hash TEXT,
                    event_type TEXT DEFAULT 'autosre.phase34.completed',
                    stream_seq INTEGER,
                    report_path TEXT,
                    published_at TEXT NOT NULL
                );
            """)
            try:
                conn.execute("ALTER TABLE published_results ADD COLUMN report_hash TEXT;")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE published_results ADD COLUMN event_type TEXT DEFAULT 'autosre.phase34.completed';")
            except sqlite3.OperationalError:
                pass

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_published_parent
                ON published_results (parent_event_id);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_published_semantic
                ON published_results (parent_event_id, report_hash, event_type);
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS incident_processing (
                    parent_event_id TEXT PRIMARY KEY,
                    correlation_id TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    input_payload_hash TEXT NOT NULL,
                    processing_status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    claimed_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    failed_at TEXT,
                    pipeline_run_id TEXT,
                    report_path TEXT,
                    report_hash TEXT,
                    result_event_id TEXT,
                    last_error_code TEXT,
                    last_error_message TEXT
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_proc_status
                ON incident_processing (processing_status);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_proc_incident
                ON incident_processing (incident_id);
            """)
            conn.commit()

    def has_event(self, event_id: str) -> bool:
        """Checks if event_id already exists in the store."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM received_events WHERE event_id = ? LIMIT 1;",
                (event_id,)
            )
            return cursor.fetchone() is not None

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single event record by event_id."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM received_events WHERE event_id = ? LIMIT 1;",
                (event_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def find_payload(self, incident_id: str, payload_hash: str) -> Optional[Dict[str, Any]]:
        """Finds any existing event record matching incident_id and payload_hash."""
        with self._connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM received_events
                WHERE incident_id = ? AND payload_hash = ?
                ORDER BY rowid ASC LIMIT 1;
                """,
                (incident_id, payload_hash)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def record_received(
        self,
        event_id: str,
        incident_id: str,
        payload_hash: str,
        correlation_id: str
    ) -> None:
        """Atomically inserts initial RECEIVED event state."""
        now = _now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO received_events (
                    event_id, incident_id, payload_hash, correlation_id,
                    status, received_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    incident_id=excluded.incident_id,
                    payload_hash=excluded.payload_hash,
                    correlation_id=excluded.correlation_id,
                    updated_at=excluded.updated_at;
                """,
                (event_id, incident_id, payload_hash, correlation_id, EventStatus.RECEIVED.value, now, now)
            )
            conn.commit()

    def mark_validated(self, event_id: str) -> None:
        """Transitions event status to VALIDATED."""
        now = _now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE received_events
                SET status = ?, updated_at = ?
                WHERE event_id = ?;
                """,
                (EventStatus.VALIDATED.value, now, event_id)
            )
            conn.commit()

    def mark_staged(self, event_id: str, input_path: str) -> None:
        """Transitions event status to STAGED and stores input file path."""
        now = _now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE received_events
                SET status = ?, input_path = ?, updated_at = ?, last_error = NULL
                WHERE event_id = ?;
                """,
                (EventStatus.STAGED.value, input_path, now, event_id)
            )
            conn.commit()

    def mark_failed(self, event_id: str, error: str) -> None:
        """Transitions event status to FAILED and records the error."""
        now = _now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE received_events
                SET status = ?, last_error = ?, updated_at = ?
                WHERE event_id = ?;
                """,
                (EventStatus.FAILED.value, error, now, event_id)
            )
            conn.commit()

    def record_published(
        self,
        event_id: str,
        parent_event_id: str,
        correlation_id: str,
        incident_id: str,
        final_outcome: str,
        stream_seq: Optional[int] = None,
        report_path: Optional[str] = None,
        report_hash: Optional[str] = None,
        event_type: str = "autosre.phase34.completed"
    ) -> None:
        """Records a successfully published remediation result event."""
        now = _now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO published_results (
                    event_id, parent_event_id, correlation_id, incident_id,
                    final_outcome, report_hash, event_type, stream_seq, report_path, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    stream_seq=excluded.stream_seq,
                    report_hash=excluded.report_hash,
                    event_type=excluded.event_type,
                    published_at=excluded.published_at;
                """,
                (event_id, parent_event_id, correlation_id, incident_id, final_outcome, report_hash, event_type, stream_seq, report_path, now)
            )
            conn.commit()

    def get_published(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a published result record by event_id."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM published_results WHERE event_id = ? LIMIT 1;",
                (event_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_published_by_parent(self, parent_event_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the latest published result record for a parent event."""
        with self._connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM published_results
                WHERE parent_event_id = ?
                ORDER BY rowid DESC LIMIT 1;
                """,
                (parent_event_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def find_published_by_semantic_key(
        self,
        parent_event_id: str,
        report_hash: str,
        event_type: str = "autosre.phase34.completed"
    ) -> Optional[Dict[str, Any]]:
        """Finds any existing published result matching parent_event_id, report_hash, and event_type."""
        with self._connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM published_results
                WHERE parent_event_id = ? AND report_hash = ? AND (event_type = ? OR event_type IS NULL)
                ORDER BY rowid DESC LIMIT 1;
                """,
                (parent_event_id, report_hash, event_type)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def claim_staged_event(
        self,
        parent_event_id: Optional[str] = None,
        retry_failed: bool = False,
        recover_stale: bool = False,
        stale_timeout_seconds: float = 3600.0
    ) -> Optional[Dict[str, Any]]:
        """
        Atomically claims a STAGED incident event for processing.
        Returns a dict describing the claim outcome and candidate details.
        """
        now = _now_iso()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")

            # 1. Identify candidate STAGED event from received_events
            if parent_event_id:
                cursor = conn.execute(
                    "SELECT * FROM received_events WHERE event_id = ? AND status = ? LIMIT 1;",
                    (parent_event_id, EventStatus.STAGED.value)
                )
                rec_row = cursor.fetchone()
                if not rec_row:
                    conn.execute("COMMIT;")
                    return None
                candidate = dict(rec_row)
            else:
                # Find oldest STAGED event not yet completed
                cursor = conn.execute(
                    """
                    SELECT r.* FROM received_events r
                    LEFT JOIN incident_processing p ON r.event_id = p.parent_event_id
                    WHERE r.status = ?
                      AND (
                          p.processing_status IS NULL
                          OR p.processing_status = 'PENDING'
                          OR (? = 1 AND p.processing_status = 'FAILED')
                          OR (? = 1 AND p.processing_status = 'PROCESSING')
                      )
                    ORDER BY r.rowid ASC LIMIT 1;
                    """,
                    (EventStatus.STAGED.value, 1 if retry_failed else 0, 1 if recover_stale else 0)
                )
                rec_row = cursor.fetchone()
                if not rec_row:
                    conn.execute("COMMIT;")
                    return None
                candidate = dict(rec_row)

            event_id = candidate["event_id"]

            # 2. Check existing incident_processing entry
            cursor = conn.execute(
                "SELECT * FROM incident_processing WHERE parent_event_id = ? LIMIT 1;",
                (event_id,)
            )
            proc_row = cursor.fetchone()

            if proc_row:
                current_status = proc_row["processing_status"]
                attempt_count = proc_row["attempt_count"] or 0

                if current_status == "RESULT_PUBLISHED":
                    conn.execute("COMMIT;")
                    return {
                        "status": "ALREADY_COMPLETED",
                        "parent_event_id": event_id,
                        "record": dict(proc_row)
                    }

                if current_status == "PROCESSING":
                    # Check if stale
                    is_stale = recover_stale
                    if not is_stale and proc_row["claimed_at"]:
                        try:
                            claimed_dt = datetime.fromisoformat(proc_row["claimed_at"].replace("Z", "+00:00"))
                            now_dt = datetime.now(timezone.utc)
                            if (now_dt - claimed_dt).total_seconds() > stale_timeout_seconds:
                                is_stale = True
                        except Exception:
                            pass

                    if not is_stale:
                        conn.execute("COMMIT;")
                        return {
                            "status": "ALREADY_CLAIMED",
                            "parent_event_id": event_id,
                            "record": dict(proc_row)
                        }

                if current_status == "FAILED" and not retry_failed:
                    conn.execute("COMMIT;")
                    return {
                        "status": "FAILED_REQUIRES_RETRY",
                        "parent_event_id": event_id,
                        "record": dict(proc_row)
                    }

                # Transition existing row to PROCESSING
                conn.execute(
                    """
                    UPDATE incident_processing
                    SET processing_status = 'PROCESSING',
                        attempt_count = attempt_count + 1,
                        claimed_at = ?,
                        started_at = ?,
                        last_error_code = NULL,
                        last_error_message = NULL
                    WHERE parent_event_id = ?;
                    """,
                    (now, now, event_id)
                )
                attempt_count += 1
            else:
                # Insert initial PROCESSING row
                attempt_count = 1
                conn.execute(
                    """
                    INSERT INTO incident_processing (
                        parent_event_id, correlation_id, incident_id,
                        input_path, input_payload_hash, processing_status,
                        attempt_count, claimed_at, started_at
                    ) VALUES (?, ?, ?, ?, ?, 'PROCESSING', 1, ?, ?);
                    """,
                    (
                        event_id,
                        candidate["correlation_id"],
                        candidate["incident_id"],
                        candidate["input_path"],
                        candidate["payload_hash"],
                        now,
                        now
                    )
                )

            conn.execute("COMMIT;")

            return {
                "status": "CLAIMED",
                "parent_event_id": event_id,
                "correlation_id": candidate["correlation_id"],
                "incident_id": candidate["incident_id"],
                "input_path": candidate["input_path"],
                "input_payload_hash": candidate["payload_hash"],
                "attempt_count": attempt_count,
                "claimed_at": now
            }

    def mark_pipeline_succeeded(
        self,
        parent_event_id: str,
        pipeline_run_id: str,
        report_path: str,
        report_hash: str
    ) -> None:
        """Transitions incident processing status to PIPELINE_SUCCEEDED."""
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE incident_processing
                SET processing_status = 'PIPELINE_SUCCEEDED',
                    pipeline_run_id = ?,
                    report_path = ?,
                    report_hash = ?,
                    last_error_code = NULL,
                    last_error_message = NULL
                WHERE parent_event_id = ?;
                """,
                (pipeline_run_id, report_path, report_hash, parent_event_id)
            )
            conn.commit()

    def mark_result_published(
        self,
        parent_event_id: str,
        result_event_id: str,
        report_hash: str
    ) -> None:
        """Transitions incident processing status to RESULT_PUBLISHED."""
        now = _now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE incident_processing
                SET processing_status = 'RESULT_PUBLISHED',
                    result_event_id = ?,
                    report_hash = ?,
                    completed_at = ?,
                    last_error_code = NULL,
                    last_error_message = NULL
                WHERE parent_event_id = ?;
                """,
                (result_event_id, report_hash, now, parent_event_id)
            )
            conn.commit()

    def mark_processing_failed(
        self,
        parent_event_id: str,
        error_code: str,
        error_message: str
    ) -> None:
        """Transitions incident processing status to FAILED with error context."""
        now = _now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE incident_processing
                SET processing_status = 'FAILED',
                    last_error_code = ?,
                    last_error_message = ?,
                    failed_at = ?
                WHERE parent_event_id = ?;
                """,
                (error_code, error_message, now, parent_event_id)
            )
            conn.commit()

    def get_processing_state(self, parent_event_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves current processing state record for parent_event_id."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM incident_processing WHERE parent_event_id = ? LIMIT 1;",
                (parent_event_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_processing_states(self) -> List[Dict[str, Any]]:
        """Lists all processing states."""
        with self._connection() as conn:
            cursor = conn.execute("SELECT * FROM incident_processing ORDER BY rowid ASC;")
            return [dict(r) for r in cursor.fetchall()]
