"""Report Indexer for Laptop 2.

Maintains canonical runtime pointers:
- runtime/report_index.json: Latest metadata pointer and rolling deduplicated execution history.
- runtime/latest_phase34_report.json: Atomic copy of the newest validated JSON report.
- runtime/latest_phase34_events.jsonl: Atomic copy of the newest validated events log.

Invariants:
1. Exactly ONE history entry per correlation_id.
2. Re-indexing the same correlation is idempotent and updates in place.
3. Historical/older replay runs do not regress the 'latest' pointer if a newer validated run exists.
4. Preserves separation of logical_target (e.g. postgres-db) and physical_execution_target (e.g. shadow-postgres-db).
"""

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_RUNTIME_DIR = "runtime"
DEFAULT_INDEX_FILE = "report_index.json"
DEFAULT_LATEST_REPORT_FILE = "latest_phase34_report.json"
DEFAULT_LATEST_EVENTS_FILE = "latest_phase34_events.jsonl"
MAX_HISTORY_ENTRIES = 50


def _atomic_write_json(file_path: str, data: Dict[str, Any]):
    """Writes a JSON file atomically using a temporary file in the same directory."""
    abs_path = os.path.abspath(file_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    dir_name = os.path.dirname(abs_path)
    temp_fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_report_", suffix=".json")
    try:
        with open(temp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, abs_path)
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise e


def _atomic_copy_file(src_path: str, dest_path: str):
    """Copies a file atomically using a temporary file in the destination directory."""
    if not os.path.exists(src_path):
        return
    abs_dest = os.path.abspath(dest_path)
    os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
    dir_name = os.path.dirname(abs_dest)
    temp_fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_copy_", suffix=os.path.splitext(abs_dest)[1])
    os.close(temp_fd)
    try:
        shutil.copy2(src_path, temp_path)
        os.replace(temp_path, abs_dest)
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise e


def _parse_iso(ts_str: Optional[str]) -> Optional[datetime]:
    """Safely parses ISO 8601 timestamp string into datetime."""
    if not ts_str:
        return None
    try:
        clean_ts = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_ts)
    except Exception:
        return None


def extract_targets(
    report_data: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[str], Optional[str]]:
    """
    Extracts logical_target and authoritative physical_execution_target from report data.
    Logical target stays the intentional target (e.g. postgres-db).
    Physical execution target comes strictly from execution/attestation evidence (e.g. shadow-postgres-db).
    """
    if not report_data or not isinstance(report_data, dict):
        return None, None

    # Logical target
    logical_target = (
        report_data.get("phase_3", {}).get("selected_intent", {}).get("target_ref", {}).get("canonical_name")
        or report_data.get("problem", {}).get("target", {}).get("canonical_name")
        or report_data.get("phase_4", {}).get("target", {}).get("canonical_name")
    )

    # Authoritative physical execution target
    p4 = report_data.get("phase_4", {})
    exec_sec = p4.get("execution", {})
    exec_res = exec_sec.get("result")

    physical_target = None
    if isinstance(exec_res, dict) and exec_res.get("target"):
        physical_target = exec_res.get("target")
    elif isinstance(exec_sec.get("target"), str):
        physical_target = exec_sec.get("target")
    elif p4.get("attestation", {}).get("reason"):
        att_reason = p4["attestation"]["reason"]
        if "shadow-postgres-db" in att_reason:
            physical_target = "shadow-postgres-db"
        elif "shadow-redis" in att_reason:
            physical_target = "shadow-redis"
        elif "shadow-rabbitmq" in att_reason:
            physical_target = "shadow-rabbitmq"

    return logical_target, physical_target


def update_report_index(
    incident_id: str,
    correlation_id: str,
    report_path: str,
    final_outcome: str,
    root_event_id: Optional[str] = None,
    parent_event_id: Optional[str] = None,
    result_event_id: Optional[str] = None,
    report_hash: Optional[str] = None,
    logical_target: Optional[str] = None,
    physical_execution_target: Optional[str] = None,
    runtime_dir: str = DEFAULT_RUNTIME_DIR,
    report_data: Optional[Dict[str, Any]] = None,
    events_path: Optional[str] = None,
    timestamp: Optional[str] = None
) -> Dict[str, Any]:
    """
    Updates runtime/report_index.json, runtime/latest_phase34_report.json,
    and runtime/latest_phase34_events.jsonl with strict deduplication and ordering.
    """
    runtime_dir_abs = os.path.abspath(runtime_dir)
    os.makedirs(runtime_dir_abs, exist_ok=True)

    index_path = os.path.join(runtime_dir_abs, DEFAULT_INDEX_FILE)
    latest_report_path = os.path.join(runtime_dir_abs, DEFAULT_LATEST_REPORT_FILE)
    latest_events_path = os.path.join(runtime_dir_abs, DEFAULT_LATEST_EVENTS_FILE)

    now_iso = timestamp or datetime.now(timezone.utc).isoformat()

    # Extract targets from report_data if not explicitly passed
    if report_data:
        extracted_logical, extracted_physical = extract_targets(report_data)
        logical_target = logical_target or extracted_logical
        physical_execution_target = physical_execution_target or extracted_physical
        if not report_hash and isinstance(report_data.get("integrity"), dict):
            report_hash = report_data["integrity"].get("report_hash")

    # Normalize relative path if inside repo root
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    rel_report_path = (
        os.path.relpath(os.path.abspath(report_path), repo_root).replace("\\", "/")
        if os.path.isabs(report_path)
        else report_path.replace("\\", "/")
    )

    new_entry = {
        "incident_id": incident_id,
        "correlation_id": correlation_id,
        "root_event_id": root_event_id,
        "parent_event_id": parent_event_id,
        "result_event_id": result_event_id,
        "report_path": rel_report_path,
        "report_hash": report_hash,
        "final_outcome": final_outcome,
        "logical_target": logical_target,
        "physical_execution_target": physical_execution_target,
        "updated_at": now_iso
    }

    # Clean out None fields from entry for tidy representation
    new_entry = {k: v for k, v in new_entry.items() if v is not None}

    # Read existing index if present
    existing_index = {}
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                existing_index = json.load(f)
        except Exception:
            existing_index = {}

    existing_latest = existing_index.get("latest")
    history = existing_index.get("history", [])
    if not isinstance(history, list):
        history = []

    # Invariant 1: Exactly ONE entry per correlation_id in history (dedup)
    history = [
        entry for entry in history
        if entry.get("correlation_id") != correlation_id
    ]
    history.insert(0, new_entry)
    history = history[:MAX_HISTORY_ENTRIES]

    # Invariant 2: Latest ordering check
    # Historical / older replay run must NOT regress 'latest' if a newer validated run exists
    should_update_latest = True
    if existing_latest and isinstance(existing_latest, dict):
        existing_ts = _parse_iso(existing_latest.get("updated_at"))
        current_ts = _parse_iso(now_iso)
        if existing_ts and current_ts:
            # If current is older than existing latest AND different correlation_id -> do not regress latest
            if current_ts < existing_ts and existing_latest.get("correlation_id") != correlation_id:
                should_update_latest = False

    latest_entry = new_entry if should_update_latest else existing_latest

    new_index_data = {
        "latest": latest_entry,
        "history": history
    }

    # 1. Atomic write to report_index.json
    try:
        _atomic_write_json(index_path, new_index_data)
        logger.info(f"Updated report index at {index_path} (latest={latest_entry.get('correlation_id')})")
    except Exception as e:
        logger.warning(f"Failed updating report index JSON: {e}")

    # 2. Atomic copy to latest_phase34_report.json (only if this run is the latest)
    if should_update_latest:
        try:
            if report_data is not None:
                _atomic_write_json(latest_report_path, report_data)
            elif os.path.exists(report_path):
                _atomic_copy_file(report_path, latest_report_path)
            logger.info(f"Updated latest report copy at {latest_report_path}")
        except Exception as e:
            logger.warning(f"Failed copying latest phase34 report: {e}")

        # 3. Atomic copy to latest_phase34_events.jsonl if available
        if events_path and os.path.exists(events_path):
            try:
                _atomic_copy_file(events_path, latest_events_path)
                logger.info(f"Updated latest events copy at {latest_events_path}")
            except Exception as e:
                logger.warning(f"Failed copying latest events log: {e}")

    return new_index_data
