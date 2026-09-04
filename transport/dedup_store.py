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
