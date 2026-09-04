"""Tests for SQLite Deduplication Store."""

import os
import tempfile
import pytest

from transport.contracts import EventStatus
from transport.dedup_store import DedupStore


@pytest.fixture
def tmp_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    store = DedupStore(db_path)
    yield store
    if os.path.exists(db_path):
        os.remove(db_path)


def test_first_event_accepted(tmp_store):
    """Verify first event is recorded properly with status RECEIVED."""
    assert not tmp_store.has_event("evt_1")
    tmp_store.record_received("evt_1", "case_1", "hash_1", "corr_1")
    assert tmp_store.has_event("evt_1")

    event = tmp_store.get_event("evt_1")
    assert event["event_id"] == "evt_1"
    assert event["incident_id"] == "case_1"
    assert event["payload_hash"] == "hash_1"
    assert event["status"] == EventStatus.RECEIVED.value


def test_status_transitions_and_staging(tmp_store):
    """Verify status transitions from RECEIVED -> VALIDATED -> STAGED."""
    tmp_store.record_received("evt_2", "case_2", "hash_2", "corr_2")
    tmp_store.mark_validated("evt_2")
    assert tmp_store.get_event("evt_2")["status"] == EventStatus.VALIDATED.value

    tmp_store.mark_staged("evt_2", "/path/to/evt_2.json")
    event = tmp_store.get_event("evt_2")
    assert event["status"] == EventStatus.STAGED.value
    assert event["input_path"] == "/path/to/evt_2.json"


def test_semantic_duplicate_lookup(tmp_store):
    """Verify find_payload locates existing staged record with same incident_id and payload_hash."""
    tmp_store.record_received("evt_orig", "case_dup", "hash_same", "corr_orig")
    tmp_store.mark_staged("evt_orig", "/path/to/evt_orig.json")

    match = tmp_store.find_payload("case_dup", "hash_same")
    assert match is not None
    assert match["event_id"] == "evt_orig"
    assert match["input_path"] == "/path/to/evt_orig.json"

    # Different hash under same incident should return None
    assert tmp_store.find_payload("case_dup", "hash_different") is None


def test_changed_payload_hash_accepted(tmp_store):
    """Verify same incident_id with changed payload hash is distinct and stored."""
    tmp_store.record_received("evt_v1", "case_mutate", "hash_v1", "corr_v1")
    tmp_store.mark_staged("evt_v1", "/path/v1.json")

    tmp_store.record_received("evt_v2", "case_mutate", "hash_v2", "corr_v2")
    tmp_store.mark_staged("evt_v2", "/path/v2.json")

    assert tmp_store.has_event("evt_v1")
    assert tmp_store.has_event("evt_v2")
    assert tmp_store.find_payload("case_mutate", "hash_v1")["event_id"] == "evt_v1"
    assert tmp_store.find_payload("case_mutate", "hash_v2")["event_id"] == "evt_v2"


def test_db_state_survives_reopen():
    """Verify persistent SQLite state survives reopening the database connection."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        store1 = DedupStore(db_path)
        store1.record_received("evt_persist", "case_p", "hash_p", "corr_p")
        store1.mark_staged("evt_persist", "/path/p.json")

        # Open second store instance on same DB file
        store2 = DedupStore(db_path)
        assert store2.has_event("evt_persist")
        event = store2.get_event("evt_persist")
        assert event["status"] == EventStatus.STAGED.value
        assert event["input_path"] == "/path/p.json"
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
