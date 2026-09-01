import json
import re


def safe_parse_json(text: str, fallback: dict | None = None) -> dict:
    """Robustly parse a JSON object from an LLM response.

    Handles markdown code fences, leading/trailing prose, and truncated output.
    Returns `fallback` (or a generic parse-failure dict) when parsing fails.
    """
    if fallback is None:
        fallback = {"logic": "Parse fallback", "triage": text or "", "stab": "", "rca": ""}

    if not text:
        return dict(fallback)

    cleaned = re.sub(r"```(?:json)?", "", text).strip()

    # 1. Direct parse
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 2. Extract the outermost {...} block
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    # 3. Repair truncated JSON (dangling "key": with no value or unbalanced quotes)
    repaired = cleaned
    # Repair dangling key like `"conf":` or `, "conf":` at end of string
    repaired = re.sub(r',\s*"[^"]+"\s*:\s*$', '', repaired)
    repaired = re.sub(r'"[^"]+"\s*:\s*$', '', repaired)

    if not repaired.endswith("}"):
        if repaired.count('"') % 2 != 0:
            repaired += '"'
        repaired += "}"

    try:
        return json.loads(repaired)
    except Exception:
        pass

    # 4. Attempt regex match after repair
    match_repaired = re.search(r"\{.*\}", repaired, re.DOTALL)
    if match_repaired:
        try:
            return json.loads(match_repaired.group(0))
        except Exception:
            pass

    return dict(fallback)
