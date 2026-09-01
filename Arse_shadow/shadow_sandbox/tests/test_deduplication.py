import pytest
import os
import tempfile
from pathlib import Path
from shadow_sandbox.persistence import SandboxPersistence

def test_deduplication_claim():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = Path(f.name)

    try:
        pers = SandboxPersistence(db_path=temp_db_path)
        hash_val = "abc123canonicalhash"
        
        # First claim must succeed
        assert pers.claim_payload(hash_val, "case_04") is True
        
        # Second claim with same payload_hash must be rejected
        assert pers.claim_payload(hash_val, "case_04") is False
    finally:
        try:
            if temp_db_path.exists():
                os.remove(temp_db_path)
        except OSError:
            pass
