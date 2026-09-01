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

from shadow_sandbox.remediation.confidence_analyzer import calculate_confidence
from shadow_sandbox.remediation.execution_harness import ExecutionHarness
from shadow_sandbox.reports.report_generator import generate_report


class TestConfidenceAnalyzer(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.history_file = os.path.join(self.test_dir, "chaos_history.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_base_score_missing_history(self):
        """Base score starts at 1.0. Missing history defaults multiplier to 0.85 -> score 0.85."""
        proposal = {"target": "shadow-auth-service", "tool": "scale_replicas"}
        score = calculate_confidence(proposal, history_path=os.path.join(self.test_dir, "non_existent.json"))
        self.assertEqual(score, 0.85)

    def test_target_penalty_postgres(self):
        """Subtract 0.15 for postgres target. (1.0 - 0.15) * 0.85 = 0.7225 -> 0.72."""
        proposal = {"target": "shadow-postgres-db", "tool": "scale_replicas"}
        score = calculate_confidence(proposal, history_path=os.path.join(self.test_dir, "non_existent.json"))
        self.assertEqual(score, 0.72)

    def test_target_penalty_redis(self):
        """Subtract 0.15 for redis target. (1.0 - 0.15) * 0.85 = 0.7225 -> 0.72."""
        proposal = {"target": "shadow-redis", "tool": "scale_replicas"}
        score = calculate_confidence(proposal, history_path=os.path.join(self.test_dir, "non_existent.json"))
        self.assertEqual(score, 0.72)

    def test_tool_penalty_run_query(self):
        """Subtract 0.10 for run_query tool. (1.0 - 0.10) * 0.85 = 0.765 -> 0.77."""
        proposal = {"target": "shadow-auth-service", "tool": "run_query"}
        score = calculate_confidence(proposal, history_path=os.path.join(self.test_dir, "non_existent.json"))
        self.assertEqual(score, 0.77)

    def test_tool_penalty_restart_container(self):
        """Subtract 0.10 for restart_container tool. (1.0 - 0.10) * 0.85 = 0.765 -> 0.77."""
        proposal = {"target": "shadow-auth-service", "tool": "restart_container"}
        score = calculate_confidence(proposal, history_path=os.path.join(self.test_dir, "non_existent.json"))
        self.assertEqual(score, 0.77)

    def test_combined_penalties_low_confidence(self):
        """postgres (-0.15) + run_query (-0.10) -> (1.0 - 0.25) * 0.85 = 0.6375 -> 0.64."""
        proposal = {"target": "shadow-postgres-db", "tool": "run_query"}
        score = calculate_confidence(proposal, history_path=os.path.join(self.test_dir, "non_existent.json"))
        self.assertEqual(score, 0.64)

    def test_custom_history_multiplier(self):
        """With tool success rate of 1.0 from history, (1.0 - 0.15) * 1.0 = 0.85."""
        history = [
            {"fault_name": "run_query", "status": "recovered"},
            {"fault_name": "run_query", "status": "recovered"}
        ]
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(history, f)

        proposal = {"target": "shadow-postgres-db", "tool": "run_query"}
        # postgres (-0.15) + run_query (-0.10) -> 0.75 * 1.0 = 0.75
        score = calculate_confidence(proposal, history_path=self.history_file)
        self.assertEqual(score, 0.75)

    def test_harness_blocks_low_confidence(self):
        """ExecutionHarness halts execution when confidence score < 0.70."""
        # Create a mock fix JSON that proposes a run_query on shadow-postgres-db
        fix_json_path = os.path.join(self.test_dir, "fix_pg.json")
        fix_content = {
            "incident_id": "test_low_confidence_incident",
            "problem": "Postgres connection pool exhausted",
            "orchestrator": {
                "technical_solution": {
                    "safety_violation": False,
                    "action_commands": ["ALTER SYSTEM SET max_connections = 200;"],
                    "confidence": 0.8
                }
            }
        }
        with open(fix_json_path, "w", encoding="utf-8") as f:
            json.dump(fix_content, f)

        # Harness with no history file -> score is 0.64 (< 0.70)
        harness = ExecutionHarness(settle_wait_s=0.1, history_path=os.path.join(self.test_dir, "non_existent.json"))
        res = harness.run(fix_json_path)

        self.assertEqual(res["gate_decision"], "BLOCKED_LOW_CONFIDENCE")
        self.assertEqual(res["confidence_score"], 0.64)
        self.assertTrue(res["human_intervention_required"])
        self.assertIn("0.64", res["message"])
        self.assertIn("below the 0.70 safety threshold", res["message"])
        self.assertIsNone(res["execution_result"])

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
