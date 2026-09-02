import os
import glob
import json
import pytest
from run_mvp_pipeline import run_single_problem

ALLOWED_OUTCOME_ENUM = {
    "SANDBOX_VERIFIED",
    "SANDBOX_FAILED_ROLLED_BACK",
    "SANDBOX_FAILED_ROLLBACK_FAILED",
    "UNSUPPORTED_IN_MVP",
    "NO_SUPPORTED_ACTION",
    "READ_ONLY_OBSERVED",
    "HUMAN_REVIEW_REQUIRED",
    "VALIDATION_FAILED"
}


def test_e2e_three_docker_golden_cases():
    """E2E Golden Case Test: Tests postgres.setting.update, redis.eviction_policy.update, container.restart."""
    golden_files = [
        "problems/case_11.json",  # postgres connection exhaustion
        "problems/case_12.json",  # redis memory eviction
        "problems/case_01.json"   # container restart/service recovery
    ]

    for gf in golden_files:
        if not os.path.exists(gf):
            pytest.skip(f"Golden test file missing: {gf}")

        res = run_single_problem(gf)
        assert res["outcome"] in ALLOWED_OUTCOME_ENUM
        assert os.path.exists(res["json_report"])
        assert os.path.exists(res["md_report"])

        with open(res["json_report"], "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["incident_id"] == res["incident_id"]
            assert data["run_id"] == res["run_id"]
            assert "phase_3" in data
            assert "phase_4" in data
            assert data["final_summary"]["outcome"] in ALLOWED_OUTCOME_ENUM


def test_e2e_all_22_cases(tmp_path):
    """E2E Test for all 22 cases: Verifies zero crashes, valid JSON & MD reports, matching IDs, valid outcome enum."""
    problems = sorted(glob.glob("problems/case_*.json"))
    assert len(problems) == 22, f"Expected 22 problem cases in problems/, found {len(problems)}"

    tmp_reports = str(tmp_path / "reports")

    for p_file in problems:
        res = run_single_problem(p_file, reports_base_dir=tmp_reports)

        assert res["outcome"] in ALLOWED_OUTCOME_ENUM, f"Invalid outcome '{res['outcome']}' for {p_file}"
        assert os.path.exists(res["json_report"]), f"Missing JSON report for {p_file}"
        assert os.path.exists(res["md_report"]), f"Missing MD report for {p_file}"

        with open(res["json_report"], "r", encoding="utf-8") as f:
            j_data = json.load(f)
            assert j_data["incident_id"] == res["incident_id"]
            assert j_data["run_id"] == res["run_id"]

            # Phase 3 responses check
            p3 = j_data.get("phase_3", {})
            assert "agents" in p3
            assert "orchestrator" in p3

            # Exact Phase 4 handoff input check
            handoff = j_data.get("phase_3_to_4_handoff", {})
            assert "exact_envelope" in handoff

            # Final summary check
            fin = j_data.get("final_summary", {})
            assert fin.get("outcome") in ALLOWED_OUTCOME_ENUM

            # Truthful reporting check: no false claims of problem_resolved if outcome is not SANDBOX_VERIFIED
            if fin.get("outcome") != "SANDBOX_VERIFIED":
                assert fin.get("problem_resolved_in_sandbox") is False
