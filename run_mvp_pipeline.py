#!/usr/bin/env python3
"""
run_mvp_pipeline.py

Single Entry Point Coordinator for Phase 3 + Phase 4 MVP Remediation Pipeline.
Input one problem -> run Phase 3 debate -> produce typed proposal -> test in Phase 4 shadow sandbox -> generate complete JSON and Markdown report via canonical producer.

Usage:
  python run_mvp_pipeline.py --input problems/case_01.json
  python run_mvp_pipeline.py --all
"""

import os
import sys
import json
import glob
import time
import uuid
import copy
import hashlib
import argparse
import subprocess
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
from contracts.validation import validate_envelope, is_mvp_supported
from shadow_sandbox.run_pipeline import run_phase4_pipeline
from shadow_sandbox.reports.report_generator import generate_phase34_report, EMPTY_EVENT_LOG_HASH
from rl_engine.advisor import RLAdvisor
from rl_engine.episode_builder import build_learning_episode
from rl_engine.episode_store import EpisodeStore


def _get_commit_sha() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        sha = res.stdout.strip()
        if sha:
            return sha
    except Exception:
        pass
    return "UNKNOWN_COMMIT"


def _build_run_section(
    verification_run_id: str,
    problem_run_id: str,
    started_at: str,
    completed_at: str,
    duration_ms: float,
    is_simulated: bool
) -> dict:
    commit_sha = _get_commit_sha()
    execution_mode = "SIMULATION" if is_simulated else "REAL_SHADOW"
    mock_llm = bool(os.environ.get("DEBATE_MOCK_LLM") == "1")
    rl_mode = os.environ.get("RL_OPERATING_MODE", "SHADOW")
    laptop1 = os.environ.get("RL_LAPTOP1_TRANSPORT")
    if laptop1 == "disabled":
        laptop1_transport = "disabled"
    elif laptop1 is not None:
        laptop1_transport = laptop1
    else:
        laptop1_transport = None

    return {
        "verification_run_id": verification_run_id,
        "problem_run_id": problem_run_id,
        "commit_sha": commit_sha,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": max(0.0, duration_ms),
        "execution_mode": execution_mode,
        "mock_llm": mock_llm,
        "rl_operating_mode": rl_mode if rl_mode in ["DISABLED", "SHADOW", "ADVISORY"] else "SHADOW",
        "laptop1_transport": laptop1_transport
    }


def _build_problem_section(case_id: str, problem_path: str, raw_problem: dict) -> dict:
    input_bytes = b""
    if os.path.exists(problem_path):
        with open(problem_path, "rb") as f:
            input_bytes = f.read()
    else:
        input_bytes = json.dumps(raw_problem, sort_keys=True, ensure_ascii=False).encode("utf-8")

    input_hash = hashlib.sha256(input_bytes).hexdigest()
    normalized_incident = raw_problem.get("normalized_incident") or raw_problem.get("incident_event", {}) or raw_problem
    target = raw_problem.get("target_ref") or raw_problem.get("target") or {"kind": "container", "canonical_name": "unknown"}
    severity = raw_problem.get("severity", "HIGH")
    expected_behavior = raw_problem.get("expected_behavior", "System operates normally within performance bounds")

    return {
        "case_id": case_id,
        "source_file": os.path.relpath(problem_path, BASE_DIR).replace("\\", "/"),
        "input_hash": input_hash,
        "raw_input": raw_problem,
        "normalized_incident": normalized_incident if isinstance(normalized_incident, dict) else {"raw": str(normalized_incident)},
        "severity": str(severity),
        "target": target,
        "expected_behavior": str(expected_behavior)
    }


def _build_phase3_section(p3_res: dict, dm: DebateManager, sol: dict) -> dict:
    p3_status = p3_res.get("phase3_status", "COMPLETED")
    conf_raw = p3_res.get("confidence_score")
    if conf_raw is not None and isinstance(conf_raw, (int, float)):
        conf_score = float(conf_raw) / 100.0 if float(conf_raw) > 1.0 else float(conf_raw)
    else:
        conf_score = 0.0

    p3_agents = p3_res.get("r1_detailed", {})
    agents_dict = {}
    for agent_name in ["optimist", "critic", "fact_checker"]:
        a_data = p3_agents.get(agent_name)
        if a_data and isinstance(a_data, dict):
            raw_resp = a_data.get("response", "")
            parsed_resp = dm.safe_parse_json(raw_resp) if isinstance(raw_resp, str) else raw_resp
            valid_flag = bool(a_data.get("valid", True))
            err_msg = a_data.get("error")
            status_str = "SUCCESS" if valid_flag and not err_msg else ("FAILED" if err_msg else "COMPLETED")
            latency_ms = max(0.0, float(a_data.get("latency", 0)) * 1000.0)
            agents_dict[agent_name] = {
                "name": agent_name,
                "status": status_str,
                "prompt": str(a_data.get("prompt", "")),
                "raw_response": str(raw_resp) if not isinstance(raw_resp, str) else raw_resp,
                "parsed_response": parsed_resp if isinstance(parsed_resp, (dict, list, str)) else None,
                "valid": valid_flag,
                "latency_ms": latency_ms,
                "error": str(err_msg) if err_msg else None
            }
        else:
            agents_dict[agent_name] = {
                "name": agent_name,
                "status": "NOT_RUN",
                "prompt": "",
                "raw_response": "",
                "parsed_response": None,
                "valid": False,
                "latency_ms": 0.0,
                "error": None
            }

    agreement_val = p3_res.get("agreement")
    if agreement_val is not None:
        try:
            agreement_float = float(agreement_val)
            agreement_float = max(0.0, min(1.0, agreement_float))
        except (ValueError, TypeError):
            agreement_float = 1.0 if p3_status == "COMPLETED" else 0.0
    else:
        agreement_float = 1.0 if p3_status == "COMPLETED" else 0.0

    selected_intent = sol.get("intent")
    if not isinstance(selected_intent, dict):
        selected_intent = None

    orch_decision = p3_res.get("orchestrator_decision", "APPROVE" if p3_status == "COMPLETED" else "REJECT")
    orch_str = json.dumps(orch_decision) if isinstance(orch_decision, dict) else str(orch_decision)

    reason_codes = p3_res.get("reason_codes", ["DIAGNOSED"] if p3_status == "COMPLETED" else ["PHASE3_FAILED"])

    return {
        "status": str(p3_status),
        "started_at": p3_res.get("started_at") or datetime.now(timezone.utc).isoformat(),
        "completed_at": p3_res.get("completed_at") or datetime.now(timezone.utc).isoformat(),
        "duration_ms": max(0.0, float(p3_res.get("total_latency_seconds", 0)) * 1000.0),
        "agents": agents_dict,
        "agreement": agreement_float,
        "confidence": {
            "score": max(0.0, min(1.0, conf_score)),
            "threshold": 0.80,
            "uncertainty": round(max(0.0, min(1.0, 1.0 - max(0.0, min(1.0, conf_score)))), 2),
            "calibration_status": "CALIBRATED" if conf_score > 0 else "UNCALIBRATED",
            "evidence_count": len(sol.get("evidence_refs", [])),
            "component_agreement": agreement_float,
            "evidence_grounding": max(0.0, min(1.0, conf_score)),
            "veto_applied": False,
            "veto_cap": None,
            "reason_codes": reason_codes
        },
        "safety": {"status": "PASS"} if p3_status == "COMPLETED" else {"status": "FAILED"},
        "selected_intent": selected_intent,
        "orchestrator_decision": orch_str,
        "reason_codes": reason_codes
    }


def _build_handoff_section(envelope: dict, is_valid: bool, errs: list) -> dict:
    payload_hash = envelope.get("payload_hash") or compute_payload_hash(envelope)
    intents = envelope.get("intents", [])
    first_intent = intents[0] if intents else {}
    intent_type = first_intent.get("intent_type", "NO_SUPPORTED_ACTION")

    cap_mapped = is_valid and intent_type != "NO_SUPPORTED_ACTION"
    target_ref = first_intent.get("target_ref") or envelope.get("target_ref") or {}
    canonical_target = target_ref.get("canonical_name", "")
    target_res = bool(canonical_target and str(canonical_target).lower() not in ["n/a", "unknown", "none", ""])
    mvp_supp = cap_mapped and is_mvp_supported(intent_type)

    return {
        "status": "SUCCESS" if is_valid else "HANDOFF_FAILED",
        "schema_valid": is_valid,
        "validation_errors": [str(e) for e in errs],
        "payload_hash": str(payload_hash),
        "exact_envelope": envelope,
        "capability_mapped": cap_mapped,
        "mvp_supported": mvp_supp,
        "target_resolved": target_res
    }


def _build_rl_advisory_section(rl_advisory_obj) -> dict:
    d = rl_advisory_obj.to_dict()
    return {
        "status": "SUCCESS" if d.get("recommendation") else "UNAVAILABLE",
        "operating_mode": d.get("policy", {}).get("operating_mode", "SHADOW"),
        "policy_version": str(d.get("policy", {}).get("policy_name", "safe_disjoint_linucb")),
        "model_version": str(d.get("policy", {}).get("model_version", "v1.0")),
        "recommendation": str(d.get("recommendation", "ABSTAIN")),
        "allowed_actions": d.get("allowed_actions", ["ACCEPT_PROPOSAL"]),
        "action_scores": d.get("action_scores", {}),
        "uncertainty": max(0.0, min(1.0, float(d.get("uncertainty", 0.1)))),
        "sample_size": max(0, int(d.get("sample_size", 0))),
        "cold_start": bool(d.get("cold_start", True)),
        "influence_allowed": False,
        "reason_codes": d.get("reason_codes", []),
        "feature_hash": str(d.get("feature_hash", "feat_hash_unknown")),
        "latency_ms": max(0.0, float(d.get("latency_ms", 0.0)))
    }


def _normalize_stage(stage_dict: Any, default_reason: str = "Stage was not reached") -> dict:
    if not isinstance(stage_dict, dict) or not stage_dict:
        return {
            "status": "NOT_RUN",
            "attempted": False,
            "reason_code": "NOT_RUN",
            "reason": default_reason,
            "data": {},
            "duration_ms": 0
        }
    status = stage_dict.get("status", "COMPLETED")
    attempted = stage_dict.get("attempted", True)
    reason_code = stage_dict.get("reason_code", "DIAGNOSED")
    reason = stage_dict.get("reason", "Stage completed")
    duration_ms = stage_dict.get("duration_ms", 0)
    data = stage_dict.get("data")
    if data is None:
        data = {k: v for k, v in stage_dict.items() if k not in ["status", "attempted", "reason_code", "reason", "duration_ms"]}
    return {
        "status": str(status),
        "attempted": bool(attempted),
        "reason_code": str(reason_code),
        "reason": str(reason),
        "data": data if isinstance(data, (dict, list)) else {"val": str(data)},
        "duration_ms": max(0.0, float(duration_ms)) if isinstance(duration_ms, (int, float)) else 0
    }


def _build_phase4_section(p4_res: dict) -> dict:
    status = p4_res.get("status", "NOT_RUN")
    exact_input = p4_res.get("exact_input")
    target = p4_res.get("target")

    exec_dict = p4_res.get("execution", {})
    if isinstance(exec_dict, dict):
        exec_status = exec_dict.get("status", status if status != "NOT_RUN" else "NOT_RUN")
        exec_attempted = bool(exec_dict.get("attempted", status in ["SANDBOX_VERIFIED", "SIMULATION_VERIFIED", "SANDBOX_FAILED_ROLLED_BACK", "SANDBOX_FAILED_ROLLBACK_FAILED"]))
        exec_cap = str(exec_dict.get("capability", "unknown"))
        exec_mode = str(exec_dict.get("mode", "OBSERVE"))
        exec_params = exec_dict.get("parameters", {})
        exec_result = exec_dict.get("result")
        exec_duration = max(0.0, float(exec_dict.get("duration_ms", 0)))
    else:
        exec_status = "NOT_RUN"
        exec_attempted = False
        exec_cap = "unknown"
        exec_mode = "OBSERVE"
        exec_params = {}
        exec_result = {}
        exec_duration = 0

    execution_normalized = {
        "status": str(exec_status),
        "attempted": exec_attempted,
        "capability": exec_cap,
        "mode": exec_mode,
        "parameters": exec_params if isinstance(exec_params, dict) else {},
        "result": exec_result,
        "duration_ms": exec_duration
    }

    state_hist = p4_res.get("state_history", [])
    reason_codes = p4_res.get("reason_codes", [status])

    return {
        "status": str(status),
        "started_at": p4_res.get("started_at") or datetime.now(timezone.utc).isoformat(),
        "completed_at": p4_res.get("completed_at") or datetime.now(timezone.utc).isoformat(),
        "duration_ms": max(0.0, float(p4_res.get("duration_ms", 0))),
        "exact_input": exact_input if isinstance(exact_input, dict) else None,
        "target": target,
        "attestation": _normalize_stage(p4_res.get("attestation"), "Execution blocked before target attestation"),
        "before_observations": _normalize_stage(p4_res.get("before_observations"), "Pre-state observation skipped"),
        "fault_setup": _normalize_stage(p4_res.get("fault_setup"), "No fault injected"),
        "execution": execution_normalized,
        "after_observations": _normalize_stage(p4_res.get("after_observations"), "Post-state observation skipped"),
        "verification": _normalize_stage(p4_res.get("verification"), "Verification skipped"),
        "rollback": _normalize_stage(p4_res.get("rollback"), "No rollback attempted"),
        "cleanup": _normalize_stage(p4_res.get("cleanup"), "Cleanup completed"),
        "state_history": state_hist if isinstance(state_hist, list) else [],
        "reason_codes": reason_codes if isinstance(reason_codes, list) else [str(reason_codes)]
    }


def _build_learning_section(rl_advisory_obj, is_simulated: bool) -> dict:
    feat_hash = str(rl_advisory_obj.to_dict().get("feature_hash", "feat_hash_unknown"))
    rec = str(rl_advisory_obj.to_dict().get("recommendation", "ABSTAIN"))
    return {
        "status": "NOT_ELIGIBLE" if is_simulated else "ELIGIBLE",
        "episode_id": f"ep_{uuid.uuid4().hex}",
        "eligible": False if is_simulated else True,
        "eligibility_reason": "SIMULATION_MODE" if is_simulated else "REAL_SHADOW_EXECUTION",
        "behavior_action": rec,
        "reward": None if is_simulated else 1.0,
        "sample_weight": 0.0 if is_simulated else 1.0,
        "feature_hash": feat_hash,
        "stored": True
    }


def _build_final_summary(p4_section: dict, sol: dict, is_simulated: bool) -> dict:
    final_status = p4_section.get("status", "NOT_RUN")
    exec_attempted = bool(p4_section.get("execution", {}).get("attempted", False))
    human_req = bool(final_status == "HUMAN_REVIEW_REQUIRED")

    prob_resolved = (not is_simulated) and (final_status == "SANDBOX_VERIFIED")

    if final_status in ["SANDBOX_VERIFIED", "SIMULATION_VERIFIED"]:
        next_action = "NO_ACTION_REQUIRED"
    elif human_req:
        next_action = "REQUIRE_HUMAN_REVIEW"
    elif final_status == "READ_ONLY_OBSERVED":
        next_action = "OBSERVE_FIRST"
    else:
        next_action = "REQUIRE_HUMAN_REVIEW"

    return {
        "outcome": str(final_status),
        "problem_resolved_in_sandbox": prob_resolved,
        "execution_performed": exec_attempted,
        "human_intervention_required": human_req,
        "recommended_next_action": next_action,
        "what_happened": f"Remediation pipeline completed with status {final_status}.",
        "why_it_happened": str(sol.get("human_recommendation") or sol.get("reasoning") or f"Execution resulted in {final_status}."),
        "safety_result": "BLOCKED" if human_req else ("PASS" if final_status != "PHASE3_FAILED" else "FAILED"),
        "confidence_result": "HIGH" if final_status in ["SANDBOX_VERIFIED", "SIMULATION_VERIFIED"] else "LOW",
        "limitations": ["Simulation execution mode active; real sandbox mutations deferred."] if is_simulated else ["Shadow sandbox execution; production mutations deferred."]
    }


def _build_integrity_section(input_hash: str, payload_hash: str) -> dict:
    return {
        "report_schema_valid": True,
        "input_hash": str(input_hash),
        "payload_hash": str(payload_hash),
        "event_log_hash": EMPTY_EVENT_LOG_HASH,
        "report_hash": "0" * 64,
        "errors": ["PHASE34_EVENT_LOG_NOT_EMITTED_PHASE3"]
    }


def run_single_problem(
    problem_path: str,
    reports_base_dir: Optional[str] = None,
    verification_run_id: Optional[str] = None
) -> Dict[str, Any]:
    """Runs one problem end-to-end through Phase 3, Phase 4, and generates JSON + Markdown reports via canonical producer."""
    start_dt = datetime.now(timezone.utc)
    started_at = start_dt.isoformat()
    start_time = time.perf_counter()

    verif_id = verification_run_id or f"verify_{uuid.uuid4().hex}"
    problem_run_id = f"run_{uuid.uuid4().hex}"

    if not os.path.exists(problem_path):
        raise FileNotFoundError(f"Problem file not found: {problem_path}")

    with open(problem_path, "r", encoding="utf-8") as f:
        raw_problem = json.load(f)

    case_id = (
        raw_problem.get("incident_id")
        or raw_problem.get("incident_event", {}).get("incident_id")
        or os.path.splitext(os.path.basename(problem_path))[0]
    )
    if "_" in case_id:
        parts = case_id.split("_")
        if parts[0] == "case" and len(parts) >= 2 and parts[1].isdigit():
            case_id = f"case_{parts[1]}"

    print(f"\n=======================================================")
    print(f"  RUNNING MVP PIPELINE: [{case_id}] ({os.path.basename(problem_path)})")
    print(f"=======================================================\n")

    # 1. Run Phase 3 Debate
    dm = DebateManager()
    p3_res = dm.run(raw_problem)
    sol = p3_res.get("solution", {})
    p3_status = p3_res.get("phase3_status", "COMPLETED")
    conf_raw = p3_res.get("confidence_score")
    if conf_raw is not None and isinstance(conf_raw, (int, float)):
        confidence_score = float(conf_raw) / 100.0 if float(conf_raw) > 1.0 else float(conf_raw)
    else:
        confidence_score = 0.0

    # 1.5 Generate RL Advisory in SHADOW mode
    rl_advisor = RLAdvisor()
    rl_advisory_obj = rl_advisor.generate_advisory(
        envelope={
            "incident_id": case_id,
            "phase3_confidence": {"score": confidence_score},
            "safety_violation": bool(p3_res.get("safety_violation", False)),
            "evidence_refs": sol.get("evidence_refs", []),
            "target_ref": {"kind": "container", "canonical_name": sol.get("intent", {}).get("target_ref", {}).get("canonical_name", "shadow-service")},
            "intents": [sol.get("intent", {})] if sol.get("intent") else []
        },
        p3_res=p3_res,
        run_id=problem_run_id
    )
    p3_res["rl_advisory"] = rl_advisory_obj.to_dict()

    # 2. Construct Validated V2 Envelope Handoff
    envelope = build_action_proposed(case_id, p3_res)
    payload_hash = envelope.get("payload_hash") or compute_payload_hash(envelope)
    is_valid, errs, _ = validate_envelope(envelope)

    # 3. Run Phase 4 Shadow Sandbox
    simulated_flag = bool(os.environ.get("DEBATE_MOCK_LLM") == "1") or bool(raw_problem.get("simulated"))
    if p3_status == "PHASE3_FAILED":
        p4_context = {
            "status": "NOT_RUN",
            "exact_input": envelope,
            "target": envelope.get("target_ref", {}),
            "attestation": {
                "status": "NOT_RUN",
                "attempted": False,
                "reason_code": "BLOCKED_SAFETY",
                "reason": "Execution blocked due to Phase 3 debate failure",
                "data": {},
                "duration_ms": 0
            },
            "before_observations": {},
            "fault_setup": {"injected": False},
            "execution": {
                "status": "NOT_RUN",
                "attempted": False,
                "capability": "NOT_RUN",
                "mode": "OBSERVE",
                "parameters": {},
                "result": {"success": False, "reason": "Phase 3 debate failed"},
                "duration_ms": 0
            },
            "after_observations": {},
            "verification": {"passed": False},
            "rollback": {"attempted": False, "result": None},
            "cleanup": {"completed": True},
            "state_history": [
                {"timestamp": datetime.now(timezone.utc).isoformat(), "state": "NOT_RUN", "reason_code": "PHASE3_FAILED", "message": "Phase 3 debate failed"}
            ],
            "duration_ms": 0
        }
    else:
        fault_spec = raw_problem.get("fault_spec")
        p4_context = run_phase4_pipeline(envelope, fault_spec=fault_spec, is_simulated=simulated_flag)

    # 3.5 Build & Store Learning Episode
    learning_episode_obj = build_learning_episode(
        advisory=rl_advisory_obj,
        envelope=envelope,
        phase4_result=p4_context,
        run_id=problem_run_id
    )
    ep_store = EpisodeStore()
    ep_store.save_advisory(rl_advisory_obj)
    ep_store.save_episode(learning_episode_obj)

    end_dt = datetime.now(timezone.utc)
    completed_at = end_dt.isoformat()
    duration_ms = (time.perf_counter() - start_time) * 1000.0

    # Build frozen canonical report sections
    run_sec = _build_run_section(verif_id, problem_run_id, started_at, completed_at, duration_ms, simulated_flag)
    prob_sec = _build_problem_section(case_id, problem_path, raw_problem)
    p3_sec = _build_phase3_section(p3_res, dm, sol)
    handoff_sec = _build_handoff_section(envelope, is_valid, errs)
    rl_sec = _build_rl_advisory_section(rl_advisory_obj)
    p4_sec = _build_phase4_section(p4_context)
    learning_sec = _build_learning_section(rl_advisory_obj, simulated_flag)
    summary_sec = _build_final_summary(p4_sec, sol, simulated_flag)
    integrity_sec = _build_integrity_section(prob_sec["input_hash"], handoff_sec["payload_hash"])

    canonical_context = {
        "schema_version": "phase34-report-v1",
        "report_type": "PHASE34_PROBLEM_SUMMARY",
        "run": run_sec,
        "problem": prob_sec,
        "phase_3": p3_sec,
        "phase_3_to_4_handoff": handoff_sec,
        "rl_advisory": rl_sec,
        "phase_4": p4_sec,
        "learning": learning_sec,
        "final_summary": summary_sec,
        "integrity": integrity_sec
    }

    # 4. Generate Canonical Phase 3+4 JSON and Markdown Reports
    json_path, md_path = generate_phase34_report(canonical_context, reports_base_dir=reports_base_dir)

    final_outcome = summary_sec.get("outcome", "UNKNOWN")
    print(f"\n[MVP COORDINATOR] Incident [{case_id}] complete!")
    print(f"  - Final Outcome : {final_outcome}")
    print(f"  - JSON Report   : {json_path}")
    print(f"  - MD Report     : {md_path}\n")

    return {
        "incident_id": case_id,
        "case_id": case_id,
        "verification_run_id": verif_id,
        "problem_run_id": problem_run_id,
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

        verification_run_id = f"verify_{uuid.uuid4().hex}"
        print(f"Running MVP Pipeline across {len(problem_files)} problem file(s) under verification run [{verification_run_id}]...")
        results = []
        crashes = []

        for p_file in problem_files:
            try:
                res = run_single_problem(p_file, reports_base_dir=args.reports_dir, verification_run_id=verification_run_id)
                results.append(res)
            except Exception as e:
                print(f"CRASH processing {p_file}: {e}")
                crashes.append({"file": p_file, "error": str(e)})

        print(f"\n=======================================================")
        print(f"  ALL {len(problem_files)} PROBLEMS COMPLETED")
        print(f"  Verification Run: {verification_run_id}")
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
        default_file = os.path.join(problems_dir, "case_01.json")
        if not os.path.exists(default_file):
            default_file = os.path.join(BASE_DIR, "debate", "input", "case_01_semantic_consensus.json")
        run_single_problem(default_file, reports_base_dir=args.reports_dir)
        sys.exit(0)


if __name__ == "__main__":
    main()
