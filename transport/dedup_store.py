"""SQLite-backed persistent deduplication store for cross-laptop transport events."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Iterator

from transport.contracts import EventStatus


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DedupStore:
    """Manages transactional state and deduplication tracking for inbound transport events."""

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
