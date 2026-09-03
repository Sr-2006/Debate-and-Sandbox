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
from contracts.validation import validate_envelope, is_mvp_supported, get_capabilities
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
    p3_status = str(p3_res.get("phase3_status", "COMPLETED"))
    scoring_meta = p3_res.get("scoring_meta", {})

    # Preserve exact deterministic confidence score
    conf_raw = p3_res.get("confidence_score")
    if conf_raw is not None and isinstance(conf_raw, (int, float)):
        conf_score = float(conf_raw) / 100.0 if float(conf_raw) > 1.0 else float(conf_raw)
    elif "confidence" in sol and isinstance(sol.get("confidence"), (int, float)):
        c_val = float(sol.get("confidence"))
        conf_score = c_val if c_val <= 1.0 else c_val / 100.0
    else:
        conf_score = 0.0
    conf_score = max(0.0, min(1.0, conf_score))

    # Threshold: exact threshold from engine / config, or null if unsupplied
    threshold_raw = p3_res.get("confidence_threshold") or scoring_meta.get("threshold") or 0.80
    threshold_val = max(0.0, min(1.0, float(threshold_raw)))

    # Agreement: null if unsupplied, do not default to 1.0
    agreement_raw = p3_res.get("agreement") if "agreement" in p3_res else scoring_meta.get("component_agreement")
    if agreement_raw is not None:
        try:
            agreement_val = max(0.0, min(1.0, float(agreement_raw)))
        except (ValueError, TypeError):
            agreement_val = None
    else:
        agreement_val = None

    # Evidence grounding: actual grounding score, not confidence score
    grounding_raw = scoring_meta.get("evidence_grounding")
    if grounding_raw is not None:
        try:
            evidence_grounding_val = max(0.0, min(1.0, float(grounding_raw)))
        except (ValueError, TypeError):
            evidence_grounding_val = 0.0
    else:
        evidence_grounding_val = 0.0

    # Safety Veto & Safety Result
    safety_violated = bool(p3_res.get("safety_violation") or scoring_meta.get("veto_applied") or scoring_meta.get("safety_violation"))
    veto_applied = safety_violated
    veto_cap = 0.64 if veto_applied else None

    if safety_violated:
        safety_dict = {
            "status": "SAFETY_VIOLATION",
            "veto_applied": True,
            "reason": scoring_meta.get("veto_reason", "Safety veto triggered")
        }
    elif p3_status == "COMPLETED":
        safety_dict = {"status": "PASS", "veto_applied": False}
    else:
        safety_dict = {"status": "UNAVAILABLE", "veto_applied": False}

    # Orchestrator Decision
    if safety_violated:
        orch_decision_val = "REJECT_SAFETY_VETO"
    elif p3_status == "PHASE3_FAILED":
        orch_decision_val = "REJECT_PHASE3_FAILED"
    else:
        orch_decision_val = p3_res.get("orchestrator_decision") or p3_res.get("execution_tier") or "TIER_1_AUTONOMOUS_EXECUTION"

    # Agents formatting
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

    selected_intent = sol.get("intent") if isinstance(sol.get("intent"), dict) else None
    reason_codes = p3_res.get("reason_codes", ["DIAGNOSED"] if p3_status == "COMPLETED" else ["PHASE3_FAILED"])

    return {
        "status": p3_status,
        "started_at": p3_res.get("started_at") or datetime.now(timezone.utc).isoformat(),
        "completed_at": p3_res.get("completed_at") or datetime.now(timezone.utc).isoformat(),
        "duration_ms": max(0.0, float(p3_res.get("total_latency_seconds", 0)) * 1000.0),
        "agents": agents_dict,
        "agreement": agreement_val,
        "confidence": {
            "score": conf_score,
            "threshold": threshold_val,
            "uncertainty": round(max(0.0, min(1.0, 1.0 - conf_score)), 2),
            "calibration_status": "CALIBRATED" if conf_score > 0 else "UNCALIBRATED",
            "evidence_count": len(sol.get("evidence_refs", [])),
            "component_agreement": agreement_val if agreement_val is not None else 0.0,
            "evidence_grounding": evidence_grounding_val,
            "veto_applied": veto_applied,
            "veto_cap": veto_cap,
            "reason_codes": reason_codes
        },
        "safety": safety_dict,
        "selected_intent": selected_intent,
        "orchestrator_decision": str(orch_decision_val),
        "reason_codes": reason_codes
    }


def _build_handoff_section(envelope: dict, is_valid: bool, errs: list) -> dict:
    payload_hash = envelope.get("payload_hash") or compute_payload_hash(envelope)
    intents = envelope.get("intents", [])
    first_intent = intents[0] if intents else {}
    intent_type = first_intent.get("intent_type", "NO_SUPPORTED_ACTION")

    capabilities_catalog = get_capabilities()
    cap_mapped = bool(intent_type in capabilities_catalog)
    mvp_supp = is_mvp_supported(intent_type)

    target_ref = first_intent.get("target_ref") or envelope.get("target_ref") or {}
    canonical_target = target_ref.get("canonical_name", "")
    target_res = bool(canonical_target and str(canonical_target).lower() not in ["n/a", "unknown", "none", ""])

    return {
        "status": "SUCCESS" if is_valid else "HANDOFF_FAILED",
        "schema_valid": bool(is_valid),
        "validation_errors": [str(e) for e in errs],
        "payload_hash": str(payload_hash),
        "exact_envelope": envelope,
        "capability_mapped": cap_mapped,
        "mvp_supported": mvp_supp,
        "target_resolved": target_res
    }


def _build_rl_advisory_section(rl_advisory_obj: Any) -> dict:
    if not rl_advisory_obj:
        return {
            "status": "UNAVAILABLE",
            "operating_mode": "DISABLED",
            "policy_version": "",
            "model_version": "",
            "recommendation": "ABSTAIN",
            "allowed_actions": [],
            "action_scores": {},
            "uncertainty": 0.0,
            "sample_size": 0,
            "cold_start": True,
            "influence_allowed": False,
            "reason_codes": ["RL_ADVISOR_UNAVAILABLE"],
            "feature_hash": "",
            "latency_ms": 0.0
        }

    d = rl_advisory_obj.to_dict() if hasattr(rl_advisory_obj, "to_dict") else rl_advisory_obj
    rec = str(d.get("recommendation") or "ABSTAIN")
    stat = "SUCCESS" if rec != "ABSTAIN" and d.get("recommendation") else "UNAVAILABLE"

    return {
        "status": stat,
        "operating_mode": str(d.get("policy", {}).get("operating_mode", d.get("operating_mode", "SHADOW"))),
        "policy_version": str(d.get("policy", {}).get("policy_name", d.get("policy_version", ""))),
        "model_version": str(d.get("policy", {}).get("model_version", d.get("model_version", ""))),
        "recommendation": rec if rec in ["ACCEPT_PROPOSAL", "OBSERVE_FIRST", "REQUIRE_HUMAN_REVIEW", "ABSTAIN"] else "ABSTAIN",
        "allowed_actions": d.get("allowed_actions", []),
        "action_scores": d.get("action_scores", {}),
        "uncertainty": max(0.0, min(1.0, float(d.get("uncertainty", 0.0)))),
        "sample_size": max(0, int(d.get("sample_size", 0))),
        "cold_start": bool(d.get("cold_start", True)),
        "influence_allowed": False,
        "reason_codes": d.get("reason_codes", []),
        "feature_hash": str(d.get("feature_hash", "")),
        "latency_ms": max(0.0, float(d.get("latency_ms", 0.0)))
    }


def _build_stage_attestation(att: Any) -> dict:
    if not isinstance(att, dict) or not att:
        return {
            "status": "NOT_RUN",
            "attempted": False,
            "reason_code": "NOT_RUN",
            "reason": "Target attestation was not run",
            "data": {},
            "duration_ms": 0
        }
    attested = att.get("attested")
    attempted = bool(att.get("attempted", attested is not None))
    if attested is True:
        status = "PASSED"
        reason_code = str(att.get("reason_code") or "PASSED")
        reason = str(att.get("reason") or "Attestation verified")
    elif attested is False:
        status = "FAILED"
        reason_code = str(att.get("reason_code") or "ATTESTATION_FAILED")
        reason = str(att.get("reason") or "Attestation check failed")
    else:
        status = str(att.get("status", "NOT_RUN" if not attempted else "UNKNOWN"))
        reason_code = str(att.get("reason_code", "NOT_RUN"))
        reason = str(att.get("reason", "Attestation status unknown"))

    data = att.get("data") if isinstance(att.get("data"), (dict, list)) else {
        k: v for k, v in att.items() if k not in ["status", "attempted", "reason_code", "reason", "duration_ms", "attested"]
    }
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(att.get("duration_ms", 0)))
    }


def _build_stage_before_observations(obs: Any) -> dict:
    if not isinstance(obs, dict) or not obs:
        return {
            "status": "NOT_RUN",
            "attempted": False,
            "reason_code": "NOT_RUN",
            "reason": "Before observation was not run",
            "data": {},
            "duration_ms": 0
        }
    attempted = bool(obs.get("attempted", bool(obs.get("data") or obs.get("status"))))
    status = str(obs.get("status", "COMPLETED" if attempted else "NOT_RUN"))
    reason_code = str(obs.get("reason_code", "OBSERVED" if attempted else "NOT_RUN"))
    reason = str(obs.get("reason", "Before state recorded" if attempted else "Not run"))
    data = obs.get("data") if isinstance(obs.get("data"), (dict, list)) else {
        k: v for k, v in obs.items() if k not in ["status", "attempted", "reason_code", "reason", "duration_ms"]
    }
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(obs.get("duration_ms", 0)))
    }


def _build_stage_fault_setup(fault: Any) -> dict:
    if not isinstance(fault, dict) or not fault:
        return {
            "status": "NOT_RUN",
            "attempted": False,
            "reason_code": "NOT_RUN",
            "reason": "No fault setup executed",
            "data": {},
            "duration_ms": 0
        }
    injected = fault.get("injected")
    attempted = bool(fault.get("attempted", injected is not None or bool(fault.get("data"))))
    status = str(fault.get("status", "COMPLETED" if (attempted and injected) else "NOT_RUN"))
    reason_code = str(fault.get("reason_code", "FAULT_INJECTED" if injected else "NOT_RUN"))
    reason = str(fault.get("reason", "Fault setup completed" if attempted else "No fault setup"))
    data = fault.get("data") if isinstance(fault.get("data"), (dict, list)) else {
        k: v for k, v in fault.items() if k not in ["status", "attempted", "reason_code", "reason", "duration_ms"]
    }
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(fault.get("duration_ms", 0)))
    }


def _build_stage_execution(exec_dict: Any) -> dict:
    if not isinstance(exec_dict, dict) or not exec_dict:
        return {
            "status": "NOT_RUN",
            "attempted": False,
            "capability": "NOT_RUN",
            "mode": "OBSERVE",
            "parameters": {},
            "result": None,
            "duration_ms": 0
        }
    attempted = bool(exec_dict.get("attempted", False))
    result = exec_dict.get("result")
    cap = str(exec_dict.get("capability", "unknown"))
    mode = str(exec_dict.get("mode", "OBSERVE"))
    params = exec_dict.get("parameters") if isinstance(exec_dict.get("parameters"), dict) else {}
    duration_ms = max(0.0, float(exec_dict.get("duration_ms", 0)))

    if not attempted:
        status = str(exec_dict.get("status", "NOT_RUN"))
    else:
        if isinstance(result, dict) and result.get("success") is False:
            status = "FAILED"
        else:
            status = str(exec_dict.get("status", "SUCCESS"))

    return {
        "status": status,
        "attempted": attempted,
        "capability": cap,
        "mode": mode,
        "parameters": params,
        "result": result if isinstance(result, (dict, str)) or result is None else {"val": str(result)},
        "duration_ms": duration_ms
    }


def _build_stage_after_observations(obs: Any) -> dict:
    if not isinstance(obs, dict) or not obs:
        return {
            "status": "NOT_RUN",
            "attempted": False,
            "reason_code": "NOT_RUN",
            "reason": "After observation was not run",
            "data": {},
            "duration_ms": 0
        }
    attempted = bool(obs.get("attempted", bool(obs.get("data") or obs.get("status"))))
    status = str(obs.get("status", "COMPLETED" if attempted else "NOT_RUN"))
    reason_code = str(obs.get("reason_code", "OBSERVED" if attempted else "NOT_RUN"))
    reason = str(obs.get("reason", "After state recorded" if attempted else "Not run"))
    data = obs.get("data") if isinstance(obs.get("data"), (dict, list)) else {
        k: v for k, v in obs.items() if k not in ["status", "attempted", "reason_code", "reason", "duration_ms"]
    }
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(obs.get("duration_ms", 0)))
    }


def _build_stage_verification(ver: Any) -> dict:
    if not isinstance(ver, dict) or not ver:
        return {
            "status": "NOT_RUN",
            "attempted": False,
            "reason_code": "NOT_RUN",
            "reason": "Verification was not run",
            "data": {},
            "duration_ms": 0
        }
    passed = ver.get("passed")
    attempted = bool(ver.get("attempted", passed is not None or ver.get("status") is not None))
    if passed is True:
        status = "PASSED"
        reason_code = str(ver.get("reason_code") or "VERIFIED_RECOVERED")
        reason = str(ver.get("reason") or "Verification passed")
    elif passed is False:
        status = "FAILED"
        reason_code = str(ver.get("reason_code") or "VERIFICATION_FAILED")
        reason = str(ver.get("reason") or "Verification failed")
    else:
        status = str(ver.get("status", "NOT_RUN" if not attempted else "UNKNOWN"))
        reason_code = str(ver.get("reason_code", "NOT_RUN"))
        reason = str(ver.get("reason", "Verification not run"))

    data = ver.get("data") if isinstance(ver.get("data"), (dict, list)) else {
        k: v for k, v in ver.items() if k not in ["status", "attempted", "reason_code", "reason", "duration_ms", "passed"]
    }
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(ver.get("duration_ms", 0)))
    }


def _build_stage_rollback(rb: Any) -> dict:
    if not isinstance(rb, dict) or not rb:
        return {
            "status": "NOT_RUN",
            "attempted": False,
            "reason_code": "NOT_RUN",
            "reason": "No rollback attempted",
            "data": {},
            "duration_ms": 0
        }
    attempted = bool(rb.get("attempted", False))
    rb_res = rb.get("result")
    success = (rb_res == "SUCCESS") or (isinstance(rb_res, dict) and rb_res.get("success") is True) or (rb.get("success") is True)

    if not attempted:
        status = "NOT_RUN"
        reason_code = "NOT_RUN"
        reason = "No rollback attempted"
    elif success:
        status = "SUCCESS"
        reason_code = str(rb.get("reason_code") or "ROLLED_BACK")
        reason = str(rb.get("reason") or "Rollback succeeded")
    else:
        status = "FAILED"
        reason_code = str(rb.get("reason_code") or "ROLLBACK_FAILED")
        reason = str(rb.get("reason") or "Rollback failed")

    data = rb.get("data") if isinstance(rb.get("data"), (dict, list)) else {
        k: v for k, v in rb.items() if k not in ["status", "attempted", "reason_code", "reason", "duration_ms", "result"]
    }
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(rb.get("duration_ms", 0)))
    }


def _build_stage_cleanup(clean: Any) -> dict:
    if not isinstance(clean, dict) or not clean:
        return {
            "status": "NOT_RUN",
            "attempted": False,
            "reason_code": "NOT_RUN",
            "reason": "Cleanup not run",
            "data": {},
            "duration_ms": 0
        }
    completed = clean.get("completed", True)
    attempted = bool(clean.get("attempted", True))
    status = "COMPLETED" if (attempted and completed) else str(clean.get("status", "NOT_RUN"))
    reason_code = str(clean.get("reason_code", "CLEANED_UP" if status == "COMPLETED" else "NOT_RUN"))
    reason = str(clean.get("reason", "Cleanup finished" if status == "COMPLETED" else "Cleanup incomplete"))
    data = clean.get("data") if isinstance(clean.get("data"), (dict, list)) else {
        k: v for k, v in clean.items() if k not in ["status", "attempted", "reason_code", "reason", "duration_ms", "completed"]
    }
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(clean.get("duration_ms", 0)))
    }


def _build_phase4_section(p4_res: dict) -> dict:
    status = str(p4_res.get("status", "NOT_RUN"))
    exact_input = p4_res.get("exact_input") if isinstance(p4_res.get("exact_input"), dict) else None
    target = p4_res.get("target")

    attestation = _build_stage_attestation(p4_res.get("attestation"))
    before_obs = _build_stage_before_observations(p4_res.get("before_observations"))
    fault_setup = _build_stage_fault_setup(p4_res.get("fault_setup"))
    execution = _build_stage_execution(p4_res.get("execution"))
    after_obs = _build_stage_after_observations(p4_res.get("after_observations"))
    verification = _build_stage_verification(p4_res.get("verification"))
    rollback = _build_stage_rollback(p4_res.get("rollback"))
    cleanup = _build_stage_cleanup(p4_res.get("cleanup"))

    state_hist = p4_res.get("state_history", [])
    reason_codes = p4_res.get("reason_codes", [status])

    return {
        "status": status,
        "started_at": p4_res.get("started_at") or datetime.now(timezone.utc).isoformat(),
        "completed_at": p4_res.get("completed_at") or datetime.now(timezone.utc).isoformat(),
        "duration_ms": max(0.0, float(p4_res.get("duration_ms", 0))),
        "exact_input": exact_input,
        "target": target,
        "attestation": attestation,
        "before_observations": before_obs,
        "fault_setup": fault_setup,
        "execution": execution,
        "after_observations": after_obs,
        "verification": verification,
        "rollback": rollback,
        "cleanup": cleanup,
        "state_history": state_hist if isinstance(state_hist, list) else [],
        "reason_codes": reason_codes if isinstance(reason_codes, list) else [str(reason_codes)]
    }


def _build_learning_section(learning_episode_obj: Any, is_simulated: bool, p4_sec: dict) -> dict:
    if is_simulated:
        return {
            "status": "NOT_ELIGIBLE",
            "episode_id": f"ep_{uuid.uuid4().hex[:12]}",
            "eligible": False,
            "eligibility_reason": "SIMULATION_MODE",
            "behavior_action": "ABSTAIN",
            "reward": None,
            "sample_weight": 0.0,
            "feature_hash": "unspecified",
            "stored": False
        }

    exec_attempted = bool(p4_sec.get("execution", {}).get("attempted", False))
    attestation_passed = (p4_sec.get("attestation", {}).get("status") == "PASSED")
    verification_status = p4_sec.get("verification", {}).get("status")
    verification_available = verification_status in ["PASSED", "FAILED"]

    if not learning_episode_obj:
        return {
            "status": "UNAVAILABLE",
            "episode_id": f"ep_{uuid.uuid4().hex[:12]}",
            "eligible": False,
            "eligibility_reason": "NO_LEARNING_EPISODE",
            "behavior_action": "ABSTAIN",
            "reward": None,
            "sample_weight": 0.0,
            "feature_hash": "unspecified",
            "stored": False
        }

    d = learning_episode_obj.to_dict() if hasattr(learning_episode_obj, "to_dict") else learning_episode_obj
    ep_learning = d.get("learning", {})

    eligible = bool(ep_learning.get("eligible", False))
    reward = ep_learning.get("reward")
    sample_weight = float(ep_learning.get("sample_weight", 0.0))
    eligibility_reason = str(ep_learning.get("eligibility_reason", "REAL_SHADOW_EXECUTION"))

    # Enforce strict learning invariants:
    if not exec_attempted or not attestation_passed or not verification_available:
        eligible = False
        reward = None
        sample_weight = 0.0

    if p4_sec.get("status") == "HUMAN_REVIEW_REQUIRED":
        eligible = False
        reward = None
        sample_weight = 0.0

    # Rollback failure must never receive positive reward
    if p4_sec.get("rollback", {}).get("status") == "FAILED" and reward is not None:
        reward = min(reward, 0.0)

    stored = bool(d.get("stored", True))
    status_str = "ELIGIBLE" if eligible else "NOT_ELIGIBLE"

    return {
        "status": status_str,
        "episode_id": str(d.get("episode_id", f"ep_{uuid.uuid4().hex[:12]}")),
        "eligible": eligible,
        "eligibility_reason": eligibility_reason,
        "behavior_action": str(ep_learning.get("behavior_action", "ABSTAIN")),
        "reward": reward,
        "sample_weight": sample_weight,
        "feature_hash": str(d.get("context", {}).get("feature_hash", "unspecified")),
        "stored": stored
    }


def _build_final_summary(p4_section: dict, p3_section: dict, sol: dict, is_simulated: bool) -> dict:
    final_outcome = p4_section.get("status", "NOT_RUN")
    exec_attempted = bool(p4_section.get("execution", {}).get("attempted", False))

    safety_dict = p3_section.get("safety", {})
    safety_violation = (safety_dict.get("status") == "SAFETY_VIOLATION") or bool(safety_dict.get("veto_applied"))
    human_req = bool(final_outcome == "HUMAN_REVIEW_REQUIRED" or safety_violation)

    ver_status = p4_section.get("verification", {}).get("status")
    # problem_resolved_in_sandbox: true ONLY for real successful verification
    prob_resolved = (not is_simulated) and (final_outcome == "SANDBOX_VERIFIED") and (ver_status == "PASSED")

    # Recommended next action
    if is_simulated and final_outcome in ["SIMULATION_VERIFIED", "SANDBOX_VERIFIED"]:
        next_action = "RUN_REAL_SHADOW_VALIDATION"
    elif final_outcome == "SANDBOX_VERIFIED":
        next_action = "APPROVE_PRODUCTION_DEPLOYMENT"
    elif human_req:
        next_action = "REQUIRE_HUMAN_REVIEW"
    elif final_outcome == "READ_ONLY_OBSERVED":
        next_action = "OBSERVE_FIRST"
    elif final_outcome == "ATTESTATION_FAILED":
        next_action = "INVESTIGATE_TARGET_HEALTH"
    elif final_outcome == "PRECONDITION_FAILED":
        next_action = "INVESTIGATE_PRECONDITIONS"
    else:
        next_action = "REQUIRE_HUMAN_REVIEW"

    # Confidence result: derived from phase_3.confidence
    conf_score = p3_section.get("confidence", {}).get("score", 0.0)
    if conf_score >= 0.85:
        confidence_res = "HIGH"
    elif conf_score >= 0.60:
        confidence_res = "MEDIUM"
    else:
        confidence_res = "LOW"

    # Safety result: derived from phase_3.safety
    if safety_violation:
        safety_res = "SAFETY_VIOLATION"
    elif safety_dict.get("status") == "PASS":
        safety_res = "PASS"
    else:
        safety_res = "UNAVAILABLE"

    # Limitations
    if is_simulated:
        limitations = [
            "Simulation execution mode active; simulation does not prove a real-world resolution.",
            "Production mutations deferred."
        ]
    else:
        limitations = [
            "Shadow sandbox execution mode active; production mutations deferred."
        ]

    return {
        "outcome": str(final_outcome),
        "problem_resolved_in_sandbox": prob_resolved,
        "execution_performed": exec_attempted,
        "human_intervention_required": human_req,
        "recommended_next_action": next_action,
        "what_happened": f"Remediation pipeline completed with status {final_outcome}.",
        "why_it_happened": str(sol.get("human_recommendation") or sol.get("reasoning") or f"Execution resulted in {final_outcome}."),
        "safety_result": safety_res,
        "confidence_result": confidence_res,
        "limitations": limitations
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
    p3_res["rl_advisory"] = rl_advisory_obj.to_dict() if hasattr(rl_advisory_obj, "to_dict") else rl_advisory_obj

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
    learning_sec = _build_learning_section(learning_episode_obj, simulated_flag, p4_sec)
    summary_sec = _build_final_summary(p4_sec, p3_sec, sol, simulated_flag)
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
