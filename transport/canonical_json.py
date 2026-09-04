"""Canonical JSON serialization and SHA-256 computation for cross-laptop transport contracts."""

import hashlib
import json
from typing import Any


def canonical_json_str(data: Any) -> str:
    """Serializes data into canonical JSON string with sorted keys, compact separators, UTF-8 unicode."""
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False
    )


def canonical_json_bytes(data: Any) -> bytes:
    """Serializes data into canonical JSON UTF-8 encoded bytes."""
    return canonical_json_str(data).encode("utf-8")


def compute_payload_sha256(payload: Any) -> str:
    """Computes SHA-256 lowercase hex string of the canonicalized payload object."""
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest().lower()
