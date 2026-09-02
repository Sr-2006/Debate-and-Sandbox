#!/usr/bin/env python3
"""
run_mvp_pipeline.py

Single Entry Point Coordinator for Phase 3 + Phase 4 MVP Remediation Pipeline.
Input one problem -> run Phase 3 debate -> produce typed proposal -> test in Phase 4 shadow sandbox -> generate complete JSON and Markdown report.

Usage:
  python run_mvp_pipeline.py --input problems/case_01.json
  python run_mvp_pipeline.py --all
"""

import os
import sys
import json
import glob
import time
import argparse
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

# Ensure pythonpath includes debate, contracts, and Arse_shadow
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

debate_dir = os.path.join(BASE_DIR, "debate")
if debate_dir not in sys.path:
    sys.path.insert(0, debate_dir)

arse_dir = os.path.join(BASE_DIR, "Arse_shadow")
if arse_dir not in sys.path:
    sys.path.insert(0, arse_dir)

from debate.debate_manager import DebateManager
from debate.action_publisher import build_action_proposed
from contracts.canonical_json import compute_payload_hash
from contracts.validation import validate_envelope
from shadow_sandbox.run_pipeline import run_phase4_pipeline
from shadow_sandbox.reports.report_generator import generate_mvp_report


def run_single_problem(problem_path: str, reports_base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Runs one problem end-to-end through Phase 3, Phase 4, and generates JSON + Markdown reports."""
    start_dt = datetime.now(timezone.utc)
    started_at = start_dt.isoformat()
    run_id = f"run_{int(start_dt.timestamp() * 1000)}"

    if not os.path.exists(problem_path):
        raise FileNotFoundError(f"Problem file not found: {problem_path}")

    with open(problem_path, "r", encoding="utf-8") as f:
        raw_problem = json.load(f)

    incident_id = (
        raw_problem.get("incident_id")
        or raw_problem.get("incident_event", {}).get("incident_id")
        or os.path.splitext(os.path.basename(problem_path))[0]
    )
    # Standardize incident_id prefix (e.g. case_01_semantic_consensus -> case_01)
    if "_" in incident_id:
        parts = incident_id.split("_")
        if parts[0] == "case" and len(parts) >= 2 and parts[1].isdigit():
            incident_id = f"case_{parts[1]}"

    print(f"\n=======================================================")
    print(f"  RUNNING MVP PIPELINE: [{incident_id}] ({os.path.basename(problem_path)})")
    print(f"=======================================================\n")

    # 1. Run Phase 3 Debate
    dm = DebateManager()
    p3_res = dm.run(raw_problem)

    sol = p3_res.get("solution", {})
    confidence_score = sol.get("confidence", p3_res.get("confidence_score", 75))
    if isinstance(confidence_score, (int, float)) and confidence_score > 1.0:
        confidence_score = confidence_score / 100.0

    p3_agents = p3_res.get("r1_detailed", {})
    p3_agents_formatted = {}
    for agent_name in ["optimist", "critic", "fact_checker"]:
        a_data = p3_agents.get(agent_name, {})
        p3_agents_formatted[agent_name] = {
            "prompt": a_data.get("prompt", ""),
            "response": dm.safe_parse_json(a_data.get("response", "")) if isinstance(a_data.get("response"), str) else a_data.get("response", {}),
            "latency_ms": int(a_data.get("latency", 0) * 1000)
        }

    p3_orch_meta = p3_res.get("orchestrator_meta", {})
    p3_context = {
        "status": "COMPLETED",
        "agents": p3_agents_formatted,
        "orchestrator": {
            "prompt": p3_orch_meta.get("prompt", ""),
            "response": sol
        },
        "confidence": {
            "score": round(confidence_score, 2),
            "reasoning": sol.get("human_recommendation") or sol.get("reasoning") or "Derived from debate evidence agreement",
            "evidence_refs": sol.get("evidence_refs", [])
        },
        "selected_intent": sol.get("intent", {}),
        "duration_ms": int(p3_res.get("total_latency_seconds", 0) * 1000)
    }

    # 2. Construct Validated V2 Envelope Handoff
    envelope = build_action_proposed(incident_id, p3_res)
    payload_hash = envelope.get("payload_hash") or compute_payload_hash(envelope)
    is_valid, errs, _ = validate_envelope(envelope)

    handoff_context = {
        "exact_envelope": envelope,
        "payload_hash": payload_hash,
        "validation": {
            "passed": is_valid,
            "errors": errs
        }
    }

    # 3. Run Phase 4 Shadow Sandbox
    fault_spec = raw_problem.get("fault_spec")
    p4_context = run_phase4_pipeline(envelope, fault_spec=fault_spec)

    end_dt = datetime.now(timezone.utc)
    completed_at = end_dt.isoformat()

    final_outcome = p4_context.get("status", "UNKNOWN")
    problem_resolved = final_outcome == "SANDBOX_VERIFIED"

    final_summary = {
        "outcome": final_outcome,
        "problem_resolved_in_sandbox": problem_resolved,
        "production_recommendation": sol.get("human_recommendation", "Human review required before production execution."),
        "limitations": ["Shadow-only execution mode active; production mutations deferred."]
    }

    report_context = {
        "report_version": "mvp-1.0",
        "incident_id": incident_id,
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "problem": {
            "raw_input": raw_problem,
            "normalized_problem": p3_res.get("normalized_incident", str(raw_problem)),
            "expected_behavior": "System recovers safely under shadow sandbox verification"
        },
        "phase_3": p3_context,
        "phase_3_to_4_handoff": handoff_context,
        "phase_4": p4_context,
        "final_summary": final_summary
    }

    # 4. Generate JSON and Markdown Reports
    json_path, md_path = generate_mvp_report(report_context, reports_base_dir=reports_base_dir)

    print(f"\n[MVP COORDINATOR] Incident [{incident_id}] complete!")
    print(f"  - Final Outcome : {final_outcome}")
    print(f"  - JSON Report   : {json_path}")
    print(f"  - MD Report     : {md_path}\n")

    return {
        "incident_id": incident_id,
        "run_id": run_id,
        "outcome": final_outcome,
        "json_report": json_path,
        "md_report": md_path
    }


def main():
    parser = argparse.ArgumentParser(description="MVP Autonomous Remediation Pipeline Coordinator")
    parser.add_argument("--input", type=str, default=None, help="Path to input problem JSON file or problems directory")
    parser.add_argument("--all", action="store_true", help="Run all 22 cases in problems directory")
    parser.add_argument("--reports-dir", type=str, default=None, help="Custom base output directory for reports")

    args = parser.parse_args()

    problems_dir = os.path.join(BASE_DIR, "problems")

    if args.all or (args.input and os.path.isdir(args.input)):
        target_dir = args.input if (args.input and os.path.isdir(args.input)) else problems_dir
        pattern = os.path.join(target_dir, "*.json")
        problem_files = sorted(glob.glob(pattern))

        if not problem_files:
            print(f"No problem JSON files found matching: {pattern}")
            sys.exit(1)

        print(f"Running MVP Pipeline across {len(problem_files)} problem file(s)...")
        results = []
        crashes = []

        for p_file in problem_files:
            try:
                res = run_single_problem(p_file, reports_base_dir=args.reports_dir)
                results.append(res)
            except Exception as e:
                print(f"CRASH processing {p_file}: {e}")
                crashes.append({"file": p_file, "error": str(e)})

        print(f"\n=======================================================")
        print(f"  ALL {len(problem_files)} PROBLEMS COMPLETED")
        print(f"  Successful Runs : {len(results)}")
        print(f"  Crashes         : {len(crashes)}")
        print(f"=======================================================\n")

        if crashes:
            sys.exit(1)
        sys.exit(0)

    elif args.input:
        try:
            run_single_problem(args.input, reports_base_dir=args.reports_dir)
            sys.exit(0)
        except Exception as e:
            print(f"Pipeline Execution Error: {e}")
            sys.exit(1)
    else:
        # Default run case_01
        default_file = os.path.join(problems_dir, "case_01.json")
        if not os.path.exists(default_file):
            default_file = os.path.join(BASE_DIR, "debate", "input", "case_01_semantic_consensus.json")
        run_single_problem(default_file, reports_base_dir=args.reports_dir)
        sys.exit(0)


if __name__ == "__main__":
    main()
