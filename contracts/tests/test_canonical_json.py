import pytest
from contracts.canonical_json import canonicalize_json, compute_payload_hash

def test_canonicalize_json_order_independent():
    obj1 = {"b": 2, "a": 1, "nested": {"z": 10, "y": 20}}
    obj2 = {"nested": {"y": 20, "z": 10}, "a": 1, "b": 2}
    
    assert canonicalize_json(obj1) == canonicalize_json(obj2)
    assert compute_payload_hash(obj1) == compute_payload_hash(obj2)

def test_payload_hash_deterministic():
    obj = {"incident_id": "case_04", "target": "postgres-db", "action": "update_setting"}
    h1 = compute_payload_hash(obj)
    h2 = compute_payload_hash(obj)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex string length
