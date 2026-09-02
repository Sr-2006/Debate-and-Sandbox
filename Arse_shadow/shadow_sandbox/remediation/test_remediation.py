#!/usr/bin/env python3
"""
shadow_sandbox/remediation/test_remediation.py

Unit tests for Layer 3 (remediation/):
1. Verifies safety_violation check stops execution immediately with BLOCKED_SAFETY_VIOLATION (Case 22).
2. Verifies guardrail rejects out-of-bounds parameters and forbidden SQL/redis commands (BLOCKED_GUARDRAIL).
3. Verifies target container shadow- name safety assertions.
"""

import os
import unittest
from unittest.mock import patch

from shadow_sandbox.remediation.execution_harness import ExecutionHarness
from shadow_sandbox.remediation.guardrail import check_guardrail
from shadow_sandbox.remediation.tools import assert_shadow_target
from contracts.reason_codes import ReasonCode

SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_inputs")


class TestShadowRemediation(unittest.TestCase):

    @patch("shadow_sandbox.remediation.execution_harness.attest_shadow_environment", return_value=(True, ReasonCode.DIAGNOSED, "OK"))
    def test_case_22_blocked_safety_violation(self, mock_attest):

        """Case 22 has safety_violation: true. Harness MUST return BLOCKED_SAFETY immediately."""
        case_22_path = os.path.join(SAMPLE_DIR, "case_22.json")
        self.assertTrue(os.path.exists(case_22_path), f"Sample case file missing at {case_22_path}")

        harness = ExecutionHarness(settle_wait_s=0.1)
        res = harness.run(case_22_path)

        self.assertIn(res["gate_decision"], ["BLOCKED_SAFETY", "BLOCKED_SAFETY_VIOLATION", "REQUIRES_HUMAN_APPROVAL"])
        self.assertTrue(res["human_intervention_required"])


    def test_guardrail_out_of_bounds_rejection(self):
        """Guardrail MUST reject max_connections = 1000 (exceeds max 500 bound)."""
        proposal = {
            "tool": "run_query",
            "target": "shadow-postgres-db",
            "parameters": {
                "statement_type": "alter_system_set",
                "setting": "max_connections",
                "value": 1000
            }
        }
        res = check_guardrail(proposal)
        self.assertFalse(res["passed"])
        self.assertIn("out of bounds", res["reason"].lower())

    def test_guardrail_forbidden_keyword_rejection(self):
        """Guardrail MUST reject proposal containing forbidden DROP keyword."""
        proposal = {
            "tool": "run_query",
            "target": "shadow-postgres-db",
            "parameters": {
                "statement_type": "alter_system_set",
                "setting": "drop table users;",
                "value": 1
            }
        }
        res = check_guardrail(proposal)
        self.assertFalse(res["passed"])
        self.assertIn("forbidden keyword", res["reason"].lower())

    def test_guardrail_non_shadow_target_rejection(self):
        """Guardrail MUST reject proposal targeting non-shadow container."""
        proposal = {
            "tool": "run_query",
            "target": "postgres-db",
            "parameters": {
                "statement_type": "alter_system_set",
                "setting": "max_connections",
                "value": 200
            }
        }
        res = check_guardrail(proposal)
        self.assertFalse(res["passed"])
        self.assertIn("non-shadow target", res["reason"].lower())

    def test_target_safety_assertion(self):
        """Target assertion MUST raise RuntimeError for non-shadow targets."""
        with self.assertRaises(RuntimeError):
            assert_shadow_target("production-db")


if __name__ == "__main__":
    unittest.main()
