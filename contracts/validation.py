import json
import re
from pathlib import Path
from typing import Dict, Any, List, Tuple
from jsonschema import validate, ValidationError
from contracts.reason_codes import ReasonCode

_SCHEMA_CACHE = None

def get_schema() -> Dict[str, Any]:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        schema_path = Path(__file__).parent / "action_proposed_v2.schema.json"
        with open(schema_path, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
    return _SCHEMA_CACHE

PLACEHOLDER_PATTERNS = [
    re.compile(r"<[^>]+>"),                    # e.g. <namespace>, <target>
    re.compile(r"path/to", re.IGNORECASE),     # e.g. path/to/cert
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bN/A\b", re.IGNORECASE),
    re.compile(r"\bFIXME\b", re.IGNORECASE),
    re.compile(r"example\.com", re.IGNORECASE)
]

def check_placeholders(obj: Any) -> List[str]:
    """Recursively checks if any string value in obj contains placeholder tokens."""
    violations = []
    if isinstance(obj, str):
        for pat in PLACEHOLDER_PATTERNS:
            if pat.search(obj):
                violations.append(f"Placeholder detected: '{obj}' matching pattern '{pat.pattern}'")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            violations.extend(check_placeholders(v))
    elif isinstance(obj, list):
        for item in obj:
            violations.extend(check_placeholders(item))
    return violations

def validate_envelope(payload: Dict[str, Any]) -> Tuple[bool, List[str], ReasonCode]:
    """
    Validates payload against JSON Schema v2, evidence binding, and placeholder rules.
    Returns: (is_valid, error_messages, reason_code)
    """
    # 1. JSON Schema validation
    try:
        schema = get_schema()
        validate(instance=payload, schema=schema)
    except ValidationError as e:
        return False, [f"JSON Schema error at {e.json_path}: {e.message}"], ReasonCode.REJECTED_SCHEMA
    except Exception as e:
        return False, [f"Schema validation error: {str(e)}"], ReasonCode.REJECTED_SCHEMA

    # 2. Check schema_version
    if payload.get("schema_version") != "2.0":
        return False, [f"Unsupported schema version: {payload.get('schema_version')}"], ReasonCode.REJECTED_SCHEMA

    # 3. Check placeholders in intents
    placeholders = check_placeholders(payload.get("intents", []))
    if placeholders:
        return False, placeholders, ReasonCode.PLACEHOLDER_DETECTED

    # 4. Check evidence binding
    for idx, intent in enumerate(payload.get("intents", [])):
        mode = intent.get("mode")
        evidence = intent.get("evidence_refs", [])
        if mode in ["MUTATE_REVERSIBLE", "MUTATE_HIGH_RISK"] and not evidence:
            return False, [f"Intent index {idx} ({intent.get('intent_type')}) requires evidence_refs"], ReasonCode.INVALID_ACTION_FORMAT

    return True, [], ReasonCode.DIAGNOSED
