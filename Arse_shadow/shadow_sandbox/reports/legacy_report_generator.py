#!/usr/bin/env python3
"""
Historical compatibility only.
Not a canonical Phase 3+4 report producer.
Must not write inside reports/<verification_run_id>/.
"""

import os
import sys
import json
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Optional

REPORTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "reports"))

def render_markdown_report(ctx: Dict[str, Any]) -> str:
    """Renders a legacy Markdown report."""
    incident_id = ctx.get("incident_id", "unknown")
    run_id = ctx.get("run_id", "unknown")
    started_at = ctx.get("started_at", "N/A")
    completed_at = ctx.get("completed_at", "N/A")

    prob = ctx.get("problem", {})
    p3 = ctx.get("phase_3", {})
    handoff = ctx.get("phase_3_to_4_handoff", {})
    p4 = ctx.get("phase_4", {})
    learning = ctx.get("learning", {})
    summary = ctx.get("final_summary", {})

    agents = p3.get("agents", {})
    optimist = agents.get("optimist", {})
    critic = agents.get("critic", {})
    fact_checker = agents.get("fact_checker", {})
    orchestrator = p3.get("orchestrator", {})
    conf = p3.get("confidence", {})

    lines = [
        f"# Incident Remediation Report: {incident_id}",
        f"**Run ID**: `{run_id}` | **Started**: {started_at} | **Completed**: {completed_at}\n",
        "---",
        "## 1. Problem Overview",
        f"- **Incident ID**: `{incident_id}`",
        f"- **Normalized Problem**: {prob.get('normalized_problem', 'N/A')}",
        f"- **Expected Behavior**: {prob.get('expected_behavior', 'System operating normally within performance bounds')}\n",
        "## 2. Original Telemetry / Input",
        "```json",
        json.dumps(prob.get("raw_input", {}), indent=2, ensure_ascii=False),
        "```\n",
        "## 3. Optimist Analysis",
        f"- **Latency**: {optimist.get('latency_ms', 0)} ms",
        "```json",
        json.dumps(optimist.get("response", {}), indent=2, ensure_ascii=False),
        "```\n",
        "## 4. Critic Analysis",
        f"- **Latency**: {critic.get('latency_ms', 0)} ms",
        "```json",
        json.dumps(critic.get("response", {}), indent=2, ensure_ascii=False),
        "```\n",
        "## 5. Fact Checker Analysis",
        f"- **Latency**: {fact_checker.get('latency_ms', 0)} ms",
        "```json",
        json.dumps(fact_checker.get("response", {}), indent=2, ensure_ascii=False),
        "```\n",
        "## 6. Debate Agreement and Disagreements",
        f"- **Component Agreement**: {conf.get('reasoning', 'Analyzed agent convergence')}",
        f"- **Phase 3 Total Latency**: {p3.get('duration_ms', 0)} ms\n",
        "## 7. Final Orchestrator Decision",
        "```json",
        json.dumps(orchestrator.get("response", {}), indent=2, ensure_ascii=False),
        "```\n",
        "## 8. Confidence Breakdown",
        f"- **Phase 3 Confidence Score**: `{conf.get('score', 0.0)}`",
        f"- **Reasoning**: {conf.get('reasoning', 'N/A')}",
        f"- **Evidence Refs**: `{conf.get('evidence_refs', [])}`\n",
        "## 9. Exact Phase 3 -> Phase 4 Envelope",
        f"- **Payload Hash**: `{handoff.get('payload_hash', 'N/A')}`",
        f"- **Validation Passed**: `{handoff.get('validation', {}).get('passed', False)}`",
        "```json",
        json.dumps(handoff.get("exact_envelope", {}), indent=2, ensure_ascii=False),
        "```\n",
        "## 10. Shadow Target and Before-State",
        f"- **Target**: `{p4.get('target', {})}`",
        f"- **Attestation**: `{p4.get('attestation', {})}`",
        "```json",
        json.dumps(p4.get("before_observations", {}), indent=2, ensure_ascii=False),
        "```\n",
        "## 11. Executed Capability and Parameters",
        f"- **Capability**: `{p4.get('execution', {}).get('capability', 'N/A')}`",
        "```json",
        json.dumps(p4.get("execution", {}).get("parameters", {}), indent=2, ensure_ascii=False),
        "```\n",
        "## 12. Execution Observations",
        f"- **Execution Duration**: {p4.get('execution', {}).get('duration_ms', 0)} ms",
        "```json",
        json.dumps(p4.get("after_observations", {}), indent=2, ensure_ascii=False),
        "```\n",
        "## 13. Verification Result",
        "```json",
        json.dumps(p4.get("verification", {}), indent=2, ensure_ascii=False),
        "```\n",
        "## 14. Rollback and Cleanup",
        f"- **Rollback Attempted**: `{p4.get('rollback', {}).get('attempted', False)}`",
        f"- **Rollback Result**: `{p4.get('rollback', {}).get('result', None)}`",
        "```json",
        json.dumps(p4.get("cleanup", {}), indent=2, ensure_ascii=False),
        "```\n",
        "## 15. Final Outcome",
        f"- **Status**: `{p4.get('status', 'UNKNOWN')}`",
        f"- **Problem Resolved in Sandbox**: `{summary.get('problem_resolved_in_sandbox', False)}`",
        f"- **Outcome Enum**: `{summary.get('outcome', 'UNKNOWN')}`\n",
        "## 16. Limitations and Human Recommendation",
        f"- **Recommendation**: {summary.get('production_recommendation', 'Human review required')}",
        f"- **Limitations**: {summary.get('limitations', [])}\n",
        "## 17. RL Advisory and Learning Feedback",
        f"- **Policy & Model Version**: `{learning.get('advisory', {}).get('policy', {}).get('policy_name', 'safe_disjoint_linucb')}` (`{learning.get('advisory', {}).get('policy', {}).get('model_version', 'cold-start')}`)",
        f"- **Operating Mode**: `{learning.get('advisory', {}).get('policy', {}).get('operating_mode', 'SHADOW')}`",
        f"- **Recommendation**: `{learning.get('advisory', {}).get('recommendation', 'ABSTAIN')}`",
        f"- **Influence Allowed**: `{learning.get('advisory', {}).get('influence_allowed', False)}`",
        f"- **Action Scores**: `{learning.get('advisory', {}).get('action_scores', {})}`",
        f"- **Uncertainty**: `{learning.get('advisory', {}).get('uncertainty', 0.5)}`",
        f"- **Sample Size**: `{learning.get('advisory', {}).get('sample_size', 0)}`",
        f"- **Cold-Start Flag**: `{learning.get('advisory', {}).get('cold_start', True)}`",
        f"- **Learning Eligibility**: `{learning.get('episode', {}).get('learning', {}).get('eligible', False)}`",
        f"- **Reward**: `{learning.get('episode', {}).get('learning', {}).get('reward', None)}`",
        f"- **Eligibility Reason**: `{learning.get('episode', {}).get('learning', {}).get('eligibility_reason', 'N/A')}`",
        f"- **Feature Hash**: `{learning.get('advisory', {}).get('feature_hash', 'N/A')}`",
        f"- **Episode ID**: `{learning.get('episode', {}).get('episode_id', 'N/A')}`\n"
    ]
    return "\n".join(lines)


def generate_mvp_report(context: Dict[str, Any], reports_base_dir: Optional[str] = None) -> Tuple[str, str]:
    """Legacy report generator function."""
    base_dir = reports_base_dir or REPORTS_DIR
    incident_id = context.get("incident_id", "case_unknown")
    run_id = context.get("run_id", f"run_{int(datetime.now(timezone.utc).timestamp())}")

    case_dir = os.path.join(base_dir, incident_id)
    os.makedirs(case_dir, exist_ok=True)

    json_path = os.path.join(case_dir, f"{run_id}.json")
    md_path = os.path.join(case_dir, f"{run_id}.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(context, f, indent=2, ensure_ascii=False)

    md_content = render_markdown_report(context)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    return json_path, md_path


def generate_report(outcome: Dict[str, Any], reports_dir: Optional[str] = None) -> str:
    """Legacy compatibility wrapper for report_generator."""
    incident_id = outcome.get("incident_id", "legacy_case")
    now_utc = datetime.now(timezone.utc)
    run_id = f"run_{now_utc.strftime('%Y%m%d_%H%M%S')}"

    ctx = {
        "report_version": "mvp-1.0",
        "incident_id": incident_id,
        "run_id": run_id,
        "started_at": outcome.get("run_timestamp") or now_utc.isoformat(),
        "completed_at": now_utc.isoformat(),
        "problem": {
            "raw_input": outcome.get("agent_proposal", {}),
            "normalized_problem": outcome.get("message", "Legacy incident execution"),
            "expected_behavior": "Shadow environment recovery"
        },
        "phase_3": {
            "status": "COMPLETED",
            "agents": {},
            "orchestrator": {},
            "confidence": {"score": outcome.get("confidence_score", 0.0), "reasoning": outcome.get("message", ""), "evidence_refs": []},
            "selected_intent": outcome.get("agent_proposal", {}),
            "duration_ms": 0
        },
        "phase_3_to_4_handoff": {
            "exact_envelope": outcome.get("agent_proposal", {}),
            "payload_hash": "legacy_hash",
            "validation": {"passed": outcome.get("gate_decision") != "BLOCKED_SCHEMA", "errors": []}
        },
        "phase_4": {
            "status": outcome.get("gate_decision", "UNKNOWN"),
            "exact_input": outcome.get("agent_proposal", {}),
            "target": outcome.get("agent_proposal", {}).get("target", {}),
            "attestation": {},
            "before_observations": outcome.get("before_state", {}),
            "fault_setup": {},
            "execution": {
                "capability": outcome.get("agent_proposal", {}).get("intent_type", "unknown"),
                "parameters": outcome.get("agent_proposal", {}).get("parameters", {}),
                "result": outcome.get("execution_result", {}),
                "duration_ms": 0
            },
            "after_observations": outcome.get("after_state", {}),
            "verification": outcome.get("guardrail_result", {}),
            "rollback": {"attempted": False, "result": None},
            "cleanup": {},
            "state_history": outcome.get("state_machine_history", []),
            "duration_ms": 0
        },
        "final_summary": {
            "outcome": outcome.get("gate_decision", "UNKNOWN"),
            "problem_resolved_in_sandbox": outcome.get("fault_cleared", False),
            "production_recommendation": outcome.get("message", "Human review required"),
            "limitations": []
        }
    }

    j_path, _ = generate_mvp_report(ctx, reports_base_dir=reports_dir or REPORTS_DIR)
    return j_path
