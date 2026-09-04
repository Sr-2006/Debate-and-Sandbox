"""Unit tests for report indexing in Laptop 2 runtime."""

import json
import os
import tempfile
import pytest

from transport.report_indexer import (
    update_report_index,
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
        "final_summary": {"outcome": "SANDBOX_VERIFIED"}
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
    assert len(res["history"]) == 1

    # 2. Check runtime/report_index.json on disk
    index_file = os.path.join(runtime_dir, DEFAULT_INDEX_FILE)
    assert os.path.exists(index_file)
    with open(index_file, "r", encoding="utf-8") as f:
        index_data = json.load(f)
    assert index_data["latest"]["incident_id"] == "order-service_51"

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


def test_update_report_index_history_rolling(tmp_path):
    runtime_dir = str(tmp_path / "runtime")
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    rep_path = str(reports_dir / "phase34_report.json")
    with open(rep_path, "w", encoding="utf-8") as f:
        json.dump({"test": 1}, f)

    for i in range(5):
        update_report_index(
            incident_id=f"case_{i}",
            correlation_id=f"corr_{i}",
            report_path=rep_path,
            final_outcome=f"OUTCOME_{i}",
            runtime_dir=runtime_dir
        )

    index_file = os.path.join(runtime_dir, DEFAULT_INDEX_FILE)
    with open(index_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["latest"]["incident_id"] == "case_4"
    assert len(data["history"]) == 5
    assert data["history"][0]["incident_id"] == "case_4"
    assert data["history"][-1]["incident_id"] == "case_0"
