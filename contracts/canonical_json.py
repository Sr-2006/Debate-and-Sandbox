import hashlib
import json
from typing import Any

def canonicalize_json(obj: Any) -> str:
    """
    Serializes a Python object into canonical JSON format.
    - Keys are sorted alphabetically at all levels.
    - Compact separators (no trailing whitespace).
    - UTF-8 encoding (ensure_ascii=False).
    """
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

def compute_payload_hash(obj: Any) -> str:
    """
    Computes a SHA-256 hexadecimal hash of the canonicalized JSON object.
    Used for payload deduplication and idempotency verification.
    """
    canonical_str = canonicalize_json(obj)
    return hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()
