import sqlite3
import os
from pathlib import Path
from typing import Dict, Any, Optional

DB_PATH = Path(__file__).resolve().parent / "sandbox_state.db"

def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target_path = db_path or DB_PATH
    conn = sqlite3.connect(str(target_path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    _init_schema(conn)
    return conn

def _init_schema(conn: sqlite3.Connection):
    with conn:
        # Inbox claims & deduplication
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inbox_claims (
                payload_hash TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL
            );
        """)
        # State transition history
        conn.execute("""
            CREATE TABLE IF NOT EXISTS state_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload_hash TEXT NOT NULL,
                incident_id TEXT NOT NULL,
                state TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                message TEXT
            );
        """)
        # Outcome store for Beta posterior updates
        conn.execute("""
            CREATE TABLE IF NOT EXISTS outcome_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                capability TEXT NOT NULL,
                target_kind TEXT NOT NULL,
                success INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                incident_id TEXT NOT NULL
            );
        """)

class SandboxPersistence:
    """SQLite-backed persistent store for claims, deduplication, and execution outcomes."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH

    def claim_payload(self, payload_hash: str, incident_id: str) -> bool:
        """Atomic claim using SQLite UNIQUE constraint on payload_hash. Returns True if claimed."""
        conn = get_db_connection(self.db_path)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO inbox_claims (payload_hash, incident_id, status) VALUES (?, ?, 'CLAIMED');",
                    (payload_hash, incident_id)
                )
            return True
        except sqlite3.IntegrityError:
            return False  # Duplicate claim ignored

    def record_transition(self, payload_hash: str, incident_id: str, state: str, reason_code: str, timestamp: str, message: str):
        conn = get_db_connection(self.db_path)
        with conn:
            conn.execute(
                "INSERT INTO state_transitions (payload_hash, incident_id, state, reason_code, timestamp, message) VALUES (?, ?, ?, ?, ?, ?);",
                (payload_hash, incident_id, state, reason_code, timestamp, message)
            )

    def record_outcome(self, capability: str, target_kind: str, success: bool, incident_id: str, timestamp: str):
        conn = get_db_connection(self.db_path)
        with conn:
            conn.execute(
                "INSERT INTO outcome_history (capability, target_kind, success, incident_id, timestamp) VALUES (?, ?, ?, ?, ?);",
                (capability, target_kind, 1 if success else 0, incident_id, timestamp)
            )

    def get_capability_history(self, capability: str, target_kind: str = "container") -> Dict[str, int]:
        """Returns total successes and failures for Beta prior updating."""
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*), SUM(success) FROM outcome_history WHERE capability = ? AND target_kind = ?;",
            (capability, target_kind)
        )
        row = cursor.fetchone()
        total = row[0] or 0
        successes = row[1] or 0
        failures = total - successes
        return {"total": total, "successes": successes, "failures": failures}
