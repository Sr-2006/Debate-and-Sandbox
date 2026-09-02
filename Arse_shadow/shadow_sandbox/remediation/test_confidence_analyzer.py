#!/usr/bin/env python3
"""
shadow_sandbox/remediation/test_confidence_analyzer.py

Unit tests for Confidence Ratio Analyser (Task 1), Harness Integration (Task 2),
and Report Generator Exposure (Task 3).
"""

import os
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from contracts.reason_codes import ReasonCode


from shadow_sandbox.remediation.confidence_analyzer import ConfidenceAnalyzer, calculate_confidence

from shadow_sandbox.remediation.execution_harness import ExecutionHarness
from shadow_sandbox.reports.report_generator import generate_report


class TestConfidenceAnalyzer(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_missing_history_returns_insufficient_history(self):
        """When history sample count < 20, reason_code is INSUFFICIENT_HISTORY."""
        analyzer = ConfidenceAnalyzer()
        res = analyzer.calculate_confidence("postgres.setting.update", "container")
        self.assertFalse(res["has_sufficient_history"])
        self.assertEqual(res["reason_code"], "INSUFFICIENT_HISTORY")

    @patch("shadow_sandbox.remediation.execution_harness.attest_shadow_environment", return_value=(True, ReasonCode.DIAGNOSED, "OK"))
    def test_harness_blocks_insufficient_history(self, mock_attest):
        """ExecutionHarness halts execution when history is insufficient (< 20 samples)."""
        fix_json_path = os.path.join(self.test_dir, "fix_pg.json")
        fix_content = {
            "incident_id": "test_insufficient_history_incident",
            "problem": "Target Service: `postgres-db`. Postgres connection pool exhausted",
            "orchestrator": {
                "technical_solution": {
                    "safety_violation": False,
                    "action_commands": ["postgres.setting.update: {\"setting_name\": \"max_connections\", \"value\": \"200\"}"],
                    "confidence": 0.8
                }
            }
        }
        with open(fix_json_path, "w", encoding="utf-8") as f:
            json.dump(fix_content, f)

        harness = ExecutionHarness(settle_wait_s=0.1)
        res = harness.run(fix_json_path)

        self.assertEqual(res["gate_decision"], "INSUFFICIENT_HISTORY")
        self.assertTrue(res["human_intervention_required"])
        self.assertIn("INSUFFICIENT_HISTORY", res["gate_decision"])


    def test_report_generator_exposes_confidence_and_blocked(self):
        """Report generator includes root level confidence_score and sets human_intervention_required=True for BLOCKED."""
        outcome = {
            "incident_id": "test_report_confidence",
            "gate_decision": "BLOCKED_LOW_CONFIDENCE",
            "confidence_score": 0.64,
            "human_intervention_required": True,
            "message": "Execution halted: Confidence ratio 0.64 is below the 0.70 safety threshold.",
            "performance": {}
        }

        report_path = generate_report(outcome, reports_dir=self.test_dir)
        self.assertTrue(os.path.exists(report_path))

        with open(report_path, "r", encoding="utf-8") as f:
            report_data = json.load(f)

        self.assertIn("confidence_score", report_data)
        self.assertEqual(report_data["confidence_score"], 0.64)
        self.assertEqual(report_data["gate_decision"], "BLOCKED_LOW_CONFIDENCE")
        self.assertTrue(report_data["human_intervention_required"])


if __name__ == "__main__":
    unittest.main()

