"""Unit tests for report indexing in Laptop 2 runtime."""

import json
import os
import tempfile
import pytest

from transport.report_indexer import (
    update_report_index,
    extract_targets,
    DEFAULT_INDEX_FILE,
    DEFAULT_LATEST_REPORT_FILE,
    DEFAULT_LATEST_EVENTS_FILE
)


def test_update_report_index_creates_files(tmp_path):
    runtime_dir = str(tmp_path / "runtime")
    reports_dir = tmp_path / "reports" / "verify_123" / "cases" / "order-service_51"
    reports_dir.mkdir(parents=True, exist_ok=True)

    report_path = str(reports_dir / "phase34_report.json")
    events_path = str(reports_dir / "phase34_events.jsonl")

    sample_report = {
        "schema_version": "phase34-report-v1",
        "problem": {"case_id": "order-service_51"},
        "final_summary": {"outcome": "SANDBOX_VERIFIED"},
        "phase_3": {
            "selected_intent": {
                "target_ref": {"canonical_name": "postgres-db"}
            }
        },
        "phase_4": {
            "execution": {
                "result": {"target": "shadow-postgres-db"}
            }
        }
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(sample_report, f, indent=2)

    with open(events_path, "w", encoding="utf-8") as f:
        f.write('{"event": "INIT"}\n{"event": "COMPLETED"}\n')

    res = update_report_index(
        incident_id="order-service_51",
        correlation_id="corr_test_abc123",
        report_path=report_path,
        final_outcome="SANDBOX_VERIFIED",
        runtime_dir=runtime_dir,
        report_data=sample_report,
        events_path=events_path
    )

    # 1. Assert return value
    assert res["latest"]["incident_id"] == "order-service_51"
    assert res["latest"]["correlation_id"] == "corr_test_abc123"
    assert res["latest"]["final_outcome"] == "SANDBOX_VERIFIED"
    assert res["latest"]["logical_target"] == "postgres-db"
    assert res["latest"]["physical_execution_target"] == "shadow-postgres-db"
    assert len(res["history"]) == 1

    # 2. Check runtime/report_index.json on disk
    index_file = os.path.join(runtime_dir, DEFAULT_INDEX_FILE)
    assert os.path.exists(index_file)
    with open(index_file, "r", encoding="utf-8") as f:
        index_data = json.load(f)
    assert index_data["latest"]["incident_id"] == "order-service_51"
    assert index_data["latest"]["logical_target"] == "postgres-db"
    assert index_data["latest"]["physical_execution_target"] == "shadow-postgres-db"

    # 3. Check runtime/latest_phase34_report.json on disk
    latest_report_file = os.path.join(runtime_dir, DEFAULT_LATEST_REPORT_FILE)
    assert os.path.exists(latest_report_file)
    with open(latest_report_file, "r", encoding="utf-8") as f:
        latest_report = json.load(f)
    assert latest_report["problem"]["case_id"] == "order-service_51"

    # 4. Check runtime/latest_phase34_events.jsonl on disk
    latest_events_file = os.path.join(runtime_dir, DEFAULT_LATEST_EVENTS_FILE)
    assert os.path.exists(latest_events_file)
    with open(latest_events_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert '{"event": "COMPLETED"}' in content


def test_update_report_index_deduplicates_by_correlation_id(tmp_path):
    """Verify that calling update_report_index multiple times for the same correlation_id updates in place without duplicates."""
    runtime_dir = str(tmp_path / "runtime")
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    rep_path = str(reports_dir / "phase34_report.json")
    with open(rep_path, "w", encoding="utf-8") as f:
        json.dump({"test": 1}, f)

    # First indexing call
    update_report_index(
        incident_id="order-service_51",
        correlation_id="corr_repeat_123",
        report_path=rep_path,
        final_outcome="SANDBOX_VERIFIED",
        runtime_dir=runtime_dir
    )

    # Second indexing call for exact same correlation_id (e.g. re-indexing / updated result)
    res = update_report_index(
        incident_id="order-service_51",
        correlation_id="corr_repeat_123",
        report_path=rep_path,
        final_outcome="SANDBOX_VERIFIED",
        runtime_dir=runtime_dir
    )

    # Exactly 1 entry in history
    assert len(res["history"]) == 1
    assert res["history"][0]["correlation_id"] == "corr_repeat_123"

    index_file = os.path.join(runtime_dir, DEFAULT_INDEX_FILE)
    with open(index_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["history"]) == 1


def test_update_report_index_historical_older_replay_does_not_regress_latest(tmp_path):
    """Verify an older historical run does not overwrite a newer validated latest pointer."""
    runtime_dir = str(tmp_path / "runtime")
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    rep_path = str(reports_dir / "phase34_report.json")
    with open(rep_path, "w", encoding="utf-8") as f:
        json.dump({"case": "new"}, f)

    # 1. New run at 12:00
    update_report_index(
        incident_id="order-service_51",
        correlation_id="corr_new_2026_09_04",
        report_path=rep_path,
        final_outcome="SANDBOX_VERIFIED",
        runtime_dir=runtime_dir,
        timestamp="2026-09-04T12:00:00Z"
    )

    # 2. Replay an older historical run from 08:00
    res = update_report_index(
        incident_id="order-service_51",
        correlation_id="corr_old_replay_2026_09_04",
        report_path=rep_path,
        final_outcome="SIMULATION_VERIFIED",
        runtime_dir=runtime_dir,
        timestamp="2026-09-04T08:00:00Z"
    )

    # History contains both runs
    assert len(res["history"]) == 2
    # But latest remains the newer run (corr_new_2026_09_04)
    assert res["latest"]["correlation_id"] == "corr_new_2026_09_04"
    assert res["latest"]["final_outcome"] == "SANDBOX_VERIFIED"


def test_extract_targets_authoritative():
    """Verify logical_target is extracted from intent/problem, and physical_execution_target from execution/attestation."""
    report_data = {
        "problem": {"target": {"canonical_name": "postgres-db"}},
        "phase_3": {
            "selected_intent": {
                "target_ref": {"canonical_name": "postgres-db"}
            }
        },
        "phase_4": {
            "attestation": {
                "reason": "Attestation verified for container shadow-postgres-db (status=running)"
            },
            "execution": {
                "result": {
                    "target": "shadow-postgres-db",
                    "sql": "ALTER SYSTEM SET max_connections = 200;"
                }
            }
        }
    }
    logical, physical = extract_targets(report_data)
    assert logical == "postgres-db"
    assert physical == "shadow-postgres-db"
