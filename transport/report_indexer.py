"""Report Indexer for Laptop 2.

Maintains canonical runtime pointers:
- runtime/report_index.json: Latest metadata pointer and rolling execution history.
- runtime/latest_phase34_report.json: Atomic copy of the newest validated JSON report.
- runtime/latest_phase34_events.jsonl: Atomic copy of the newest validated events log.
"""

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_RUNTIME_DIR = "runtime"
DEFAULT_INDEX_FILE = "report_index.json"
DEFAULT_LATEST_REPORT_FILE = "latest_phase34_report.json"
DEFAULT_LATEST_EVENTS_FILE = "latest_phase34_events.jsonl"
MAX_HISTORY_ENTRIES = 50


def _atomic_write_json(file_path: str, data: Dict[str, Any]):
    """Writes a JSON file atomically using a temporary file in the same directory."""
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    dir_name = os.path.dirname(os.path.abspath(file_path))
    temp_fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_report_", suffix=".json")
    try:
        with open(temp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        # On Windows, os.replace handles atomic overwrite
        os.replace(temp_path, file_path)
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
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    dir_name = os.path.dirname(os.path.abspath(dest_path))
    temp_fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_copy_", suffix=os.path.splitext(dest_path)[1])
    os.close(temp_fd)
    try:
        shutil.copy2(src_path, temp_path)
        os.replace(temp_path, dest_path)
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise e


def update_report_index(
    incident_id: str,
    correlation_id: str,
    report_path: str,
    final_outcome: str,
    runtime_dir: str = DEFAULT_RUNTIME_DIR,
    report_data: Optional[Dict[str, Any]] = None,
    events_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Updates runtime/report_index.json, runtime/latest_phase34_report.json,
    and runtime/latest_phase34_events.jsonl.
    """
    runtime_dir_abs = os.path.abspath(runtime_dir)
    os.makedirs(runtime_dir_abs, exist_ok=True)

    index_path = os.path.join(runtime_dir_abs, DEFAULT_INDEX_FILE)
    latest_report_path = os.path.join(runtime_dir_abs, DEFAULT_LATEST_REPORT_FILE)
    latest_events_path = os.path.join(runtime_dir_abs, DEFAULT_LATEST_EVENTS_FILE)

    now_iso = datetime.now(timezone.utc).isoformat()

    # Normalize relative path if inside repo root
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    rel_report_path = os.path.relpath(os.path.abspath(report_path), repo_root).replace("\\", "/") if os.path.isabs(report_path) else report_path.replace("\\", "/")

    latest_entry = {
        "incident_id": incident_id,
        "correlation_id": correlation_id,
        "report_path": rel_report_path,
        "final_outcome": final_outcome,
        "updated_at": now_iso
    }

    # Read existing index if present
    existing_index = {}
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                existing_index = json.load(f)
        except Exception:
            existing_index = {}

    history = existing_index.get("history", [])
    if not isinstance(history, list):
        history = []

    # Prepend new entry to history and limit to MAX_HISTORY_ENTRIES
    history.insert(0, latest_entry)
    history = history[:MAX_HISTORY_ENTRIES]

    new_index_data = {
        "latest": latest_entry,
        "history": history
    }

    # 1. Atomic write to report_index.json
    try:
        _atomic_write_json(index_path, new_index_data)
        logger.info(f"Updated report index at {index_path}")
    except Exception as e:
        logger.warning(f"Failed updating report index JSON: {e}")

    # 2. Atomic copy to latest_phase34_report.json
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
