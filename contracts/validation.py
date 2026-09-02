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

import yaml

_CAPABILITIES_CACHE = None

def get_capabilities() -> Dict[str, Any]:
    global _CAPABILITIES_CACHE
    if _CAPABILITIES_CACHE is None:
        cap_path = Path(__file__).parent / "capabilities.yaml"
        with open(cap_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            _CAPABILITIES_CACHE = data.get("capabilities", {})
    return _CAPABILITIES_CACHE

def validate_envelope(payload: Dict[str, Any]) -> Tuple[bool, List[str], ReasonCode]:
    """
    Validates payload against JSON Schema v2, evidence binding, placeholder rules, and semantic capability catalog contracts.
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
    intents = payload.get("intents", [])
    if not intents:
        return False, ["Payload contains no structured intents"], ReasonCode.INVALID_ACTION_FORMAT

    placeholders = check_placeholders(intents)
    if placeholders:
        return False, placeholders, ReasonCode.PLACEHOLDER_DETECTED

    capabilities = get_capabilities()

    # 4. Semantic Validation per intent
    for idx, intent in enumerate(intents):
        intent_type = intent.get("intent_type")
        mode = intent.get("mode")
        target_ref = intent.get("target_ref", {})
        target_name = target_ref.get("canonical_name", "")
        target_kind = target_ref.get("kind", "")
        params = intent.get("parameters", {})
        evidence = intent.get("evidence_refs", [])

        # Target resolution check
        if not target_name or target_name.lower() in ["n/a", "unknown", "unknown-service", "none", "api-gateway"] and intent_type != "ingress.rate_limit.patch":
            return False, [f"Intent index {idx} ({intent_type}) has unresolved target name '{target_name}'"], ReasonCode.BLOCKED_TARGET_UNRESOLVED

        # Capability catalog check
        if intent_type not in capabilities:
            return False, [f"Intent index {idx} specifies unmapped capability '{intent_type}'"], ReasonCode.BLOCKED_UNKNOWN_CAPABILITY

        cap_meta = capabilities[intent_type]
        cat_mode = cap_meta.get("mode")
        if cat_mode and mode != cat_mode:
            return False, [f"Intent index {idx} mode '{mode}' does not match catalog authoritative mode '{cat_mode}' for capability '{intent_type}'"], ReasonCode.BLOCKED_INVALID_PARAMETERS

        supported_targets = cap_meta.get("supported_targets", [])

        if target_kind and target_kind not in supported_targets:
            return False, [f"Intent index {idx} target kind '{target_kind}' not in supported targets {supported_targets} for capability '{intent_type}'"], ReasonCode.BLOCKED_TARGET_UNRESOLVED

        # Detailed Parameter Schema Validation
        param_schema = cap_meta.get("parameters_schema", {})
        for required_param, p_rules in param_schema.items():
            if isinstance(p_rules, dict):
                # Required parameter check
                if p_rules.get("default") is None:
                    if required_param not in params or params[required_param] is None or str(params[required_param]).strip() == "":
                        return False, [f"Intent index {idx} ({intent_type}) missing required parameter '{required_param}'"], ReasonCode.BLOCKED_INVALID_PARAMETERS
                
                # Parameter type & value check
                if required_param in params:
                    val = params[required_param]
                    expected_type = p_rules.get("type")
                    if expected_type == "integer" and not (isinstance(val, int) and not isinstance(val, bool)):
                        return False, [f"Intent index {idx} ({intent_type}) parameter '{required_param}' must be integer, got {type(val).__name__}"], ReasonCode.BLOCKED_INVALID_PARAMETERS
                    elif expected_type == "string" and not isinstance(val, str):
                        return False, [f"Intent index {idx} ({intent_type}) parameter '{required_param}' must be string, got {type(val).__name__}"], ReasonCode.BLOCKED_INVALID_PARAMETERS

                    min_bound = p_rules.get("minimum")
                    if min_bound is not None and isinstance(val, (int, float)) and val < min_bound:
                        return False, [f"Intent index {idx} ({intent_type}) parameter '{required_param}' value {val} below minimum bound {min_bound}"], ReasonCode.BLOCKED_INVALID_PARAMETERS

                    enum_vals = p_rules.get("enum")
                    if enum_vals and val not in enum_vals:
                        return False, [f"Intent index {idx} ({intent_type}) parameter '{required_param}' value '{val}' not in allowed enum values {enum_vals}"], ReasonCode.BLOCKED_INVALID_PARAMETERS

        # Evidence binding check for mutative operations
        if mode in ["MUTATE_REVERSIBLE", "MUTATE_HIGH_RISK"] and not evidence:
            return False, [f"Intent index {idx} ({intent_type}) missing evidence_refs for mutative action"], ReasonCode.BLOCKED_MISSING_EVIDENCE


    return True, [], ReasonCode.DIAGNOSED

