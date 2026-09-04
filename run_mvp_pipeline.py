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

from debate.config import AUTONOMOUS_THRESHOLD
from debate.debate_manager import DebateManager
from debate.action_publisher import build_action_proposed
from contracts.canonical_json import compute_payload_hash
from contracts.validation import validate_envelope, is_mvp_supported, get_capabilities
from shadow_sandbox.run_pipeline import run_phase4_pipeline
from shadow_sandbox.reports.report_generator import (
    generate_phase34_report,
    EMPTY_EVENT_LOG_HASH,
    load_report_schema,
    get_format_checker,
    ReportContractError,
)
from shadow_sandbox.reports.event_recorder import (
    Phase34EventRecorder,
    EventContractError,
    EventWriteError,
)
from rl_engine.advisor import RLAdvisor
from rl_engine.episode_builder import build_learning_episode
from rl_engine.episode_store import EpisodeStore
from jsonschema import Draft7Validator
from transport.pipeline_adapter import normalize_for_pipeline
from transport.report_indexer import update_report_index


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
    p3_status = str(p3_res.get("phase3_status")) if "phase3_status" in p3_res else "PHASE3_FAILED"
    scoring_meta = p3_res.get("scoring_meta", {})

    # Deterministic confidence score: null if missing/uncalculated
    conf_raw = p3_res.get("confidence_score")
    if conf_raw is not None and isinstance(conf_raw, (int, float)):
        c_val = float(conf_raw) / 100.0 if float(conf_raw) > 1.0 else float(conf_raw)
        conf_score = max(0.0, min(1.0, c_val))
    else:
        conf_score = None

    if conf_score is not None:
        threshold_val = float(AUTONOMOUS_THRESHOLD / 100.0)
        uncertainty_val = round(max(0.0, min(1.0, 1.0 - conf_score)), 2)
        calibration_status = "CALIBRATED" if conf_score > 0 else "UNCALIBRATED"
    else:
        threshold_val = None
        uncertainty_val = None
        calibration_status = "UNAVAILABLE"

    # Agreement: null if unsupplied
    agreement_raw = p3_res.get("agreement") if "agreement" in p3_res else scoring_meta.get("component_agreement")
    if agreement_raw is not None:
        try:
            agreement_val = max(0.0, min(1.0, float(agreement_raw)))
        except (ValueError, TypeError):
            agreement_val = None
    else:
        agreement_val = None

    # Evidence grounding: null if unsupplied
    grounding_raw = scoring_meta.get("evidence_grounding")
    if grounding_raw is not None:
        try:
            evidence_grounding_val = max(0.0, min(1.0, float(grounding_raw)))
        except (ValueError, TypeError):
            evidence_grounding_val = None
    else:
        evidence_grounding_val = None

    # Safety Veto & Safety Result
    safety_violated = bool(p3_res.get("safety_violation") or scoring_meta.get("veto_applied") or scoring_meta.get("safety_violation"))
    safety_evaluated = bool(p3_res.get("safety_evaluated", False))
    veto_applied = safety_violated
    veto_cap = 0.64 if veto_applied else None

    if safety_violated:
        safety_dict = {
            "status": "SAFETY_VIOLATION",
            "veto_applied": True,
            "reason": scoring_meta.get("veto_reason", "Safety veto triggered")
        }
    elif safety_evaluated:
        safety_dict = {"status": "PASS", "veto_applied": False}
    else:
        safety_dict = {"status": "UNAVAILABLE", "veto_applied": False}

    # Orchestrator Decision
    if safety_violated:
        orch_decision_val = "REJECT_SAFETY_VETO"
    elif p3_status == "PHASE3_FAILED":
        orch_decision_val = "REJECT_PHASE3_FAILED"
    elif p3_res.get("orchestrator_decision"):
        orch_decision_val = str(p3_res["orchestrator_decision"])
    elif p3_res.get("execution_tier"):
        orch_decision_val = str(p3_res["execution_tier"])
    else:
        orch_decision_val = "UNAVAILABLE"

    # Agents formatting
    p3_agents = p3_res.get("r1_detailed", {})
    agents_dict = {}
    for agent_name in ["optimist", "critic", "fact_checker"]:
        a_data = p3_agents.get(agent_name)
        if a_data and isinstance(a_data, dict):
            raw_resp = a_data.get("response", "")
            parsed_resp = dm.safe_parse_json(raw_resp) if isinstance(raw_resp, str) else raw_resp
            valid_flag = bool(a_data.get("valid", False))
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
    reason_codes = p3_res.get("reason_codes", ["UNAVAILABLE"] if p3_status != "COMPLETED" else [])

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
            "uncertainty": uncertainty_val,
            "calibration_status": calibration_status,
            "evidence_count": len(sol.get("evidence_refs", [])),
            "component_agreement": agreement_val,
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


def _build_rl_advisory_section(rl_advisory_obj: Any, rl_error: Optional[str] = None) -> dict:
    if not rl_advisory_obj or rl_error is not None:
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
            "reason_codes": ["RL_ADVISOR_EXCEPTION"],
            "feature_hash": "UNAVAILABLE",
            "latency_ms": 0.0
        }

    d = rl_advisory_obj.to_dict() if hasattr(rl_advisory_obj, "to_dict") else rl_advisory_obj
    rec = str(d.get("recommendation") or "ABSTAIN")

    return {
        "status": "SUCCESS",
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
    if not isinstance(att, dict) or "attempted" not in att:
        return {
            "status": "UNAVAILABLE",
            "attempted": False,
            "reason_code": "UNAVAILABLE",
            "reason": "Attestation stage data unavailable",
            "data": {},
            "duration_ms": 0
        }
    attempted = bool(att.get("attempted"))
    attested = att.get("attested")
    if attempted:
        if attested is True or att.get("status") == "PASSED":
            status = "PASSED"
            reason_code = str(att.get("reason_code") or "PASSED")
            reason = str(att.get("reason") or "Attestation verified")
        else:
            status = "FAILED"
            reason_code = str(att.get("reason_code") or "ATTESTATION_FAILED")
            reason = str(att.get("reason") or "Attestation check failed")
    else:
        status = "NOT_RUN"
        reason_code = str(att.get("reason_code") or "NOT_RUN")
        reason = str(att.get("reason") or "Target attestation was not run")

    data = att.get("data") if isinstance(att.get("data"), (dict, list)) else {}
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(att.get("duration_ms", 0)))
    }


def _build_stage_before_observations(obs: Any) -> dict:
    if not isinstance(obs, dict) or "attempted" not in obs:
        return {
            "status": "UNAVAILABLE",
            "attempted": False,
            "reason_code": "UNAVAILABLE",
            "reason": "Before observation data unavailable",
            "data": {},
            "duration_ms": 0
        }
    attempted = bool(obs.get("attempted"))
    status = str(obs.get("status", "COMPLETED" if attempted else "NOT_RUN"))
    reason_code = str(obs.get("reason_code", "OBSERVED" if attempted else "NOT_RUN"))
    reason = str(obs.get("reason", "Before state recorded" if attempted else "Not run"))
    data = obs.get("data") if isinstance(obs.get("data"), (dict, list)) else {}
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(obs.get("duration_ms", 0)))
    }


def _build_stage_fault_setup(fault: Any) -> dict:
    if not isinstance(fault, dict) or "attempted" not in fault:
        return {
            "status": "UNAVAILABLE",
            "attempted": False,
            "reason_code": "UNAVAILABLE",
            "reason": "Fault setup stage data unavailable",
            "data": {},
            "duration_ms": 0
        }
    attempted = bool(fault.get("attempted"))
    injected = fault.get("injected")
    status = str(fault.get("status", "COMPLETED" if (attempted and injected) else ("NOT_RUN" if not attempted else "FAILED")))
    reason_code = str(fault.get("reason_code", "FAULT_INJECTED" if injected else "NOT_RUN"))
    reason = str(fault.get("reason", "Fault setup completed" if attempted else "No fault setup"))
    data = fault.get("data") if isinstance(fault.get("data"), (dict, list)) else {}
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(fault.get("duration_ms", 0)))
    }


def _build_stage_execution(exec_dict: Any) -> dict:
    if not isinstance(exec_dict, dict) or "attempted" not in exec_dict:
        return {
            "status": "UNAVAILABLE",
            "attempted": False,
            "capability": "NOT_RUN",
            "mode": "OBSERVE",
            "parameters": {},
            "result": None,
            "duration_ms": 0
        }
    attempted = bool(exec_dict.get("attempted"))
    result = exec_dict.get("result")
    cap = str(exec_dict.get("capability", "unknown"))
    mode = str(exec_dict.get("mode", "OBSERVE"))
    params = exec_dict.get("parameters") if isinstance(exec_dict.get("parameters"), dict) else {}
    duration_ms = max(0.0, float(exec_dict.get("duration_ms", 0)))

    if not attempted:
        status = "NOT_RUN"
        result_val = None
    else:
        if result is None and "status" not in exec_dict:
            status = "UNKNOWN"
            result_val = None
        elif isinstance(result, dict) and result.get("success") is False:
            status = "FAILED"
            result_val = result
        else:
            status = str(exec_dict.get("status", "SUCCESS"))
            result_val = result if isinstance(result, (dict, str)) else {"val": str(result)}

    return {
        "status": status,
        "attempted": attempted,
        "capability": cap,
        "mode": mode,
        "parameters": params,
        "result": result_val,
        "duration_ms": duration_ms
    }


def _build_stage_after_observations(obs: Any) -> dict:
    if not isinstance(obs, dict) or "attempted" not in obs:
        return {
            "status": "UNAVAILABLE",
            "attempted": False,
            "reason_code": "UNAVAILABLE",
            "reason": "After observation data unavailable",
            "data": {},
            "duration_ms": 0
        }
    attempted = bool(obs.get("attempted"))
    status = str(obs.get("status", "COMPLETED" if attempted else "NOT_RUN"))
    reason_code = str(obs.get("reason_code", "OBSERVED" if attempted else "NOT_RUN"))
    reason = str(obs.get("reason", "After state recorded" if attempted else "Not run"))
    data = obs.get("data") if isinstance(obs.get("data"), (dict, list)) else {}
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(obs.get("duration_ms", 0)))
    }


def _build_stage_verification(ver: Any) -> dict:
    if not isinstance(ver, dict) or "attempted" not in ver:
        return {
            "status": "UNAVAILABLE",
            "attempted": False,
            "reason_code": "UNAVAILABLE",
            "reason": "Verification stage data unavailable",
            "data": {},
            "duration_ms": 0
        }
    attempted = bool(ver.get("attempted"))
    passed = ver.get("passed")
    if attempted:
        if passed is True or ver.get("status") == "PASSED":
            status = "PASSED"
            reason_code = str(ver.get("reason_code") or "VERIFIED_RECOVERED")
            reason = str(ver.get("reason") or "Verification passed")
        else:
            status = "FAILED"
            reason_code = str(ver.get("reason_code") or "VERIFICATION_FAILED")
            reason = str(ver.get("reason") or "Verification failed")
    else:
        status = "NOT_RUN"
        reason_code = str(ver.get("reason_code") or "NOT_RUN")
        reason = str(ver.get("reason") or "Verification not run")

    data = ver.get("data") if isinstance(ver.get("data"), (dict, list)) else {}
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(ver.get("duration_ms", 0)))
    }


def _build_stage_rollback(rb: Any) -> dict:
    if not isinstance(rb, dict) or "attempted" not in rb:
        return {
            "status": "UNAVAILABLE",
            "attempted": False,
            "reason_code": "UNAVAILABLE",
            "reason": "Rollback stage data unavailable",
            "data": {},
            "duration_ms": 0
        }
    attempted = bool(rb.get("attempted"))
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

    data = rb.get("data") if isinstance(rb.get("data"), (dict, list)) else {}
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(rb.get("duration_ms", 0)))
    }


def _build_stage_cleanup(clean: Any) -> dict:
    if not isinstance(clean, dict) or "attempted" not in clean or "completed" not in clean:
        return {
            "status": "UNAVAILABLE",
            "attempted": False,
            "reason_code": "UNAVAILABLE",
            "reason": "Cleanup stage data unavailable",
            "data": {},
            "duration_ms": 0
        }
    attempted = bool(clean.get("attempted"))
    completed = bool(clean.get("completed"))
    status = "COMPLETED" if (attempted and completed) else ("NOT_RUN" if not attempted else "FAILED")
    reason_code = str(clean.get("reason_code", "CLEANED_UP" if status == "COMPLETED" else ("NOT_RUN" if not attempted else "FAILED")))
    reason = str(clean.get("reason", "Cleanup finished" if status == "COMPLETED" else "Cleanup not run"))
    data = clean.get("data") if isinstance(clean.get("data"), (dict, list)) else {}
    return {
        "status": status,
        "attempted": attempted,
        "reason_code": reason_code,
        "reason": reason,
        "data": data,
        "duration_ms": max(0.0, float(clean.get("duration_ms", 0)))
    }


def _build_failed_phase4_section(envelope: Optional[dict] = None) -> dict:
    reason_msg = "Phase 4 not entered because Phase 3 failed"
    not_run_stage = {
        "status": "NOT_RUN",
        "attempted": False,
        "reason_code": "PHASE3_FAILED",
        "reason": reason_msg,
        "data": {},
        "duration_ms": 0
    }
    exec_stage = {
        "status": "NOT_RUN",
        "attempted": False,
        "reason_code": "PHASE3_FAILED",
        "reason": reason_msg,
        "capability": "NOT_RUN",
        "mode": "OBSERVE",
        "parameters": {},
        "result": None,
        "duration_ms": 0
    }
    return {
        "status": "NOT_RUN",
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_ms": 0.0,
        "exact_input": envelope,
        "target": envelope.get("target_ref", {}) if isinstance(envelope, dict) else {},
        "attestation": copy.deepcopy(not_run_stage),
        "before_observations": copy.deepcopy(not_run_stage),
        "fault_setup": copy.deepcopy(not_run_stage),
        "execution": copy.deepcopy(exec_stage),
        "after_observations": copy.deepcopy(not_run_stage),
        "verification": copy.deepcopy(not_run_stage),
        "rollback": copy.deepcopy(not_run_stage),
        "cleanup": copy.deepcopy(not_run_stage),
        "state_history": [
            {"timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "state": "NOT_RUN", "reason_code": "PHASE3_FAILED", "message": reason_msg}
        ],
        "reason_codes": ["PHASE3_FAILED"]
    }


def _build_phase4_section(p4_res: dict) -> dict:
    status = str(p4_res.get("status", "NOT_RUN"))
    if status == "PHASE3_FAILED":
        return _build_failed_phase4_section(p4_res.get("exact_input"))

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

    started_at_str = str(p4_res.get("started_at") or datetime.now(timezone.utc).isoformat()).replace("+00:00", "Z")
    completed_at_str = str(p4_res.get("completed_at") or datetime.now(timezone.utc).isoformat()).replace("+00:00", "Z")

    return {
        "status": status,
        "started_at": started_at_str,
        "completed_at": completed_at_str,
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


def _build_learning_section(learning_episode_obj: Any, is_simulated: bool, p4_sec: dict, episode_stored: bool = False) -> dict:
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
            "stored": bool(episode_stored)
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
            "stored": bool(episode_stored)
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
        "stored": bool(episode_stored)
    }


def _build_final_summary(p4_section: dict, p3_section: dict, sol: dict, is_simulated: bool) -> dict:
    final_outcome = p4_section.get("status", "NOT_RUN")
    exec_attempted = bool(p4_section.get("execution", {}).get("attempted", False))

    safety_dict = p3_section.get("safety", {})
    safety_violation = (safety_dict.get("status") == "SAFETY_VIOLATION") or bool(safety_dict.get("veto_applied"))
    human_req = bool(final_outcome == "HUMAN_REVIEW_REQUIRED" or safety_violation)

    ver_status = p4_section.get("verification", {}).get("status")
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

    # Confidence result
    conf_score = p3_section.get("confidence", {}).get("score")
    if conf_score is None:
        confidence_res = "UNAVAILABLE"
    elif conf_score >= 0.85:
        confidence_res = "HIGH"
    elif conf_score >= 0.60:
        confidence_res = "MEDIUM"
    else:
        confidence_res = "LOW"

    # Safety result
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
    started_at = start_dt.isoformat().replace("+00:00", "Z")
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

    # Instantiate Event Recorder immediately
    recorder = Phase34EventRecorder(
        verification_run_id=verif_id,
        problem_run_id=problem_run_id,
        case_id=case_id
    )

    # 1. Record PROBLEM_RECEIVED immediately after successfully parsing the input
    prob_sec = _build_problem_section(case_id, problem_path, raw_problem)
    recorder.record(
        phase="INPUT",
        component="coordinator",
        event="PROBLEM_RECEIVED",
        status="COMPLETED",
        reason_code="PROBLEM_LOADED",
        duration_ms=0.0,
        details={"case_id": case_id, "source_file": prob_sec["source_file"], "severity": prob_sec["severity"]}
    )

    # 2. Record PHASE3_STARTED immediately before DebateManager.run()
    recorder.record(
        phase="PHASE_3",
        component="debate_manager",
        event="PHASE3_STARTED",
        status="STARTED",
        reason_code="STARTED",
        duration_ms=0.0,
        details={}
    )

    # Normalize problem for downstream pipeline consumers without mutating original input or file
    normalized_problem = normalize_for_pipeline(raw_problem)

    # 3. Run DebateManager.run()
    dm = DebateManager()
    p3_res = dm.run(normalized_problem)
    sol = p3_res.get("solution", {})
    p3_status = str(p3_res.get("phase3_status") or "PHASE3_FAILED")

    # Immediately build the Phase 3 normalized section and record Phase 3 events
    p3_sec = _build_phase3_section(p3_res, dm, sol)

    # 3a. OPTIMIST_COMPLETED or OPTIMIST_NOT_RUN
    opt_st = p3_sec["agents"]["optimist"]
    recorder.record(
        phase="PHASE_3",
        component="agent.optimist",
        event="OPTIMIST_COMPLETED" if opt_st["status"] in ["SUCCESS", "COMPLETED", "FAILED"] else "OPTIMIST_NOT_RUN",
        status=opt_st["status"],
        reason_code=opt_st["status"],
        duration_ms=opt_st["latency_ms"],
        details={"valid": opt_st["valid"], "latency_ms": opt_st["latency_ms"]}
    )

    # 3b. CRITIC_COMPLETED or CRITIC_NOT_RUN
    crit_st = p3_sec["agents"]["critic"]
    recorder.record(
        phase="PHASE_3",
        component="agent.critic",
        event="CRITIC_COMPLETED" if crit_st["status"] in ["SUCCESS", "COMPLETED", "FAILED"] else "CRITIC_NOT_RUN",
        status=crit_st["status"],
        reason_code=crit_st["status"],
        duration_ms=crit_st["latency_ms"],
        details={"valid": crit_st["valid"], "latency_ms": crit_st["latency_ms"]}
    )

    # 3c. FACT_CHECKER_COMPLETED or FACT_CHECKER_NOT_RUN
    fc_st = p3_sec["agents"]["fact_checker"]
    recorder.record(
        phase="PHASE_3",
        component="agent.fact_checker",
        event="FACT_CHECKER_COMPLETED" if fc_st["status"] in ["SUCCESS", "COMPLETED", "FAILED"] else "FACT_CHECKER_NOT_RUN",
        status=fc_st["status"],
        reason_code=fc_st["status"],
        duration_ms=fc_st["latency_ms"],
        details={"valid": fc_st["valid"], "latency_ms": fc_st["latency_ms"]}
    )

    # 3d. CONFIDENCE_CALCULATED or CONFIDENCE_UNAVAILABLE
    conf_obj = p3_sec["confidence"]
    if conf_obj["score"] is not None:
        recorder.record(
            phase="PHASE_3",
            component="scoring_engine",
            event="CONFIDENCE_CALCULATED",
            status="SUCCESS",
            reason_code=conf_obj["calibration_status"],
            duration_ms=0.0,
            details={"score": conf_obj["score"], "threshold": conf_obj["threshold"], "calibration_status": conf_obj["calibration_status"]}
        )
    else:
        recorder.record(
            phase="PHASE_3",
            component="scoring_engine",
            event="CONFIDENCE_UNAVAILABLE",
            status="UNAVAILABLE",
            reason_code="UNAVAILABLE",
            duration_ms=0.0,
            details={"score": None, "threshold": None, "calibration_status": "UNAVAILABLE"}
        )

    # 3e. SAFETY_EVALUATED or SAFETY_UNAVAILABLE
    safety_obj = p3_sec["safety"]
    if safety_obj["status"] in ["PASS", "SAFETY_VIOLATION"]:
        recorder.record(
            phase="PHASE_3",
            component="safety_guard",
            event="SAFETY_EVALUATED",
            status=safety_obj["status"],
            reason_code="SAFETY_VIOLATION" if safety_obj.get("veto_applied") else "SAFETY_PASS",
            duration_ms=0.0,
            details={"status": safety_obj["status"], "veto_applied": safety_obj.get("veto_applied", False)}
        )
    else:
        recorder.record(
            phase="PHASE_3",
            component="safety_guard",
            event="SAFETY_UNAVAILABLE",
            status="UNAVAILABLE",
            reason_code="UNAVAILABLE",
            duration_ms=0.0,
            details={"status": "UNAVAILABLE", "veto_applied": False}
        )

    # 3f. ORCHESTRATOR_COMPLETED or PHASE3_FAILED
    if p3_status != "PHASE3_FAILED":
        recorder.record(
            phase="PHASE_3",
            component="orchestrator",
            event="ORCHESTRATOR_COMPLETED",
            status="COMPLETED",
            reason_code=p3_sec["orchestrator_decision"],
            duration_ms=p3_sec["duration_ms"],
            details={"decision": p3_sec["orchestrator_decision"], "status": p3_status}
        )
    else:
        recorder.record(
            phase="PHASE_3",
            component="orchestrator",
            event="PHASE3_FAILED",
            status="FAILED",
            reason_code="PHASE3_FAILED",
            duration_ms=p3_sec["duration_ms"],
            details={"decision": p3_sec["orchestrator_decision"], "status": "PHASE3_FAILED"}
        )

    # 4. Build and validate the exact envelope
    envelope = build_action_proposed(case_id, p3_res)
    payload_hash = envelope.get("payload_hash") or compute_payload_hash(envelope)
    is_valid, errs, _ = validate_envelope(envelope)
    handoff_sec = _build_handoff_section(envelope, is_valid, errs)

    # 5. Immediately record ENVELOPE_CREATED and ENVELOPE_VALIDATED / ENVELOPE_VALIDATION_FAILED
    recorder.record(
        phase="PHASE_3_TO_4_HANDOFF",
        component="action_publisher",
        event="ENVELOPE_CREATED",
        status="SUCCESS",
        reason_code="ENVELOPE_CREATED",
        duration_ms=0.0,
        details={"payload_hash": str(payload_hash)}
    )

    if is_valid:
        recorder.record(
            phase="PHASE_3_TO_4_HANDOFF",
            component="envelope_validator",
            event="ENVELOPE_VALIDATED",
            status="SUCCESS",
            reason_code="VALID",
            duration_ms=0.0,
            details={
                "schema_valid": is_valid,
                "payload_hash": str(payload_hash),
                "capability_mapped": handoff_sec["capability_mapped"],
                "mvp_supported": handoff_sec["mvp_supported"],
                "target_resolved": handoff_sec["target_resolved"]
            }
        )
    else:
        recorder.record(
            phase="PHASE_3_TO_4_HANDOFF",
            component="envelope_validator",
            event="ENVELOPE_VALIDATION_FAILED",
            status="FAILED",
            reason_code="INVALID",
            duration_ms=0.0,
            details={
                "schema_valid": is_valid,
                "payload_hash": str(payload_hash),
                "capability_mapped": handoff_sec["capability_mapped"],
                "mvp_supported": handoff_sec["mvp_supported"],
                "target_resolved": handoff_sec["target_resolved"]
            }
        )

    # 6. Generate the RL advisory only after envelope validation, using the exact envelope
    rl_advisory_obj = None
    rl_error = None
    try:
        rl_advisor = RLAdvisor()
        rl_advisory_obj = rl_advisor.generate_advisory(
            envelope=copy.deepcopy(envelope),
            p3_res=p3_res,
            run_id=problem_run_id
        )
    except Exception as e:
        rl_error = str(e)

    rl_sec = _build_rl_advisory_section(rl_advisory_obj, rl_error=rl_error)

    # 7. Immediately record RL_ADVISORY_CREATED or RL_ADVISORY_UNAVAILABLE
    if rl_sec["status"] == "SUCCESS":
        recorder.record(
            phase="RL_ADVISORY",
            component="rl_advisor",
            event="RL_ADVISORY_CREATED",
            status="SUCCESS",
            reason_code=rl_sec["recommendation"],
            duration_ms=rl_sec["latency_ms"],
            details={
                "status": rl_sec["status"],
                "recommendation": rl_sec["recommendation"],
                "operating_mode": rl_sec["operating_mode"]
            }
        )
    else:
        recorder.record(
            phase="RL_ADVISORY",
            component="rl_advisor",
            event="RL_ADVISORY_UNAVAILABLE",
            status="UNAVAILABLE",
            reason_code=rl_sec["recommendation"],
            duration_ms=rl_sec["latency_ms"],
            details={
                "status": rl_sec["status"],
                "recommendation": rl_sec["recommendation"],
                "operating_mode": rl_sec["operating_mode"]
            }
        )

    # 8. Phase 4 Execution
    simulated_flag = bool(os.environ.get("DEBATE_MOCK_LLM") == "1") or bool(raw_problem.get("simulated"))
    if p3_status == "PHASE3_FAILED":
        # Record PHASE4_SKIPPED and do not call run_phase4_pipeline()
        recorder.record(
            phase="PHASE_4",
            component="shadow_sandbox",
            event="PHASE4_SKIPPED",
            status="NOT_RUN",
            reason_code="PHASE3_FAILED",
            duration_ms=0.0,
            details={}
        )
        p4_context = _build_failed_phase4_section(copy.deepcopy(envelope))
    else:
        # Record PHASE4_STARTED immediately before calling run_phase4_pipeline()
        recorder.record(
            phase="PHASE_4",
            component="shadow_sandbox",
            event="PHASE4_STARTED",
            status="STARTED",
            reason_code="STARTED",
            duration_ms=0.0,
            details={}
        )
        fault_spec = raw_problem.get("fault_spec")
        p4_context = run_phase4_pipeline(copy.deepcopy(envelope), fault_spec=fault_spec, is_simulated=simulated_flag)

    # 10. Normalize Phase 4 result and record stage outcome events
    p4_sec = _build_phase4_section(p4_context)

    # 10a. TARGET_RESOLUTION_COMPLETED or TARGET_RESOLUTION_SKIPPED
    if p3_status == "PHASE3_FAILED":
        recorder.record(
            phase="PHASE_4",
            component="target_resolver",
            event="TARGET_RESOLUTION_SKIPPED",
            status="NOT_RUN",
            reason_code="PHASE3_FAILED",
            duration_ms=0.0,
            details={}
        )
    else:
        res_ok = bool(handoff_sec.get("target_resolved"))
        recorder.record(
            phase="PHASE_4",
            component="target_resolver",
            event="TARGET_RESOLUTION_COMPLETED",
            status="RESOLVED" if res_ok else "FAILED",
            reason_code="RESOLVED" if res_ok else "TARGET_UNRESOLVED",
            duration_ms=0.0,
            details={"target": p4_sec.get("target", {})}
        )

    # 10b. ATTESTATION_COMPLETED or ATTESTATION_SKIPPED
    att = p4_sec["attestation"]
    recorder.record(
        phase="PHASE_4",
        component="attestation",
        event="ATTESTATION_COMPLETED" if att["attempted"] else "ATTESTATION_SKIPPED",
        status=att["status"],
        reason_code=att["reason_code"],
        duration_ms=att["duration_ms"],
        details={"attempted": att["attempted"], "status": att["status"]}
    )

    # 10c. BEFORE_OBSERVATION_COMPLETED or BEFORE_OBSERVATION_SKIPPED
    b_obs = p4_sec["before_observations"]
    recorder.record(
        phase="PHASE_4",
        component="observation",
        event="BEFORE_OBSERVATION_COMPLETED" if b_obs["attempted"] else "BEFORE_OBSERVATION_SKIPPED",
        status=b_obs["status"],
        reason_code=b_obs["reason_code"],
        duration_ms=b_obs["duration_ms"],
        details={"attempted": b_obs["attempted"], "status": b_obs["status"]}
    )

    # 10d. FAULT_SETUP_COMPLETED or FAULT_SETUP_SKIPPED
    f_set = p4_sec["fault_setup"]
    recorder.record(
        phase="PHASE_4",
        component="fault_injector",
        event="FAULT_SETUP_COMPLETED" if f_set["attempted"] else "FAULT_SETUP_SKIPPED",
        status=f_set["status"],
        reason_code=f_set["reason_code"],
        duration_ms=f_set["duration_ms"],
        details={"attempted": f_set["attempted"], "status": f_set["status"]}
    )

    # 10e. EXECUTION_COMPLETED, EXECUTION_BLOCKED or EXECUTION_SKIPPED
    ex = p4_sec["execution"]
    if p3_sec["safety"].get("veto_applied"):
        # Machine reason code: SAFETY_VETO, human explanation inside details["reason"]
        recorder.record(
            phase="PHASE_4",
            component="executor",
            event="EXECUTION_BLOCKED",
            status="BLOCKED",
            reason_code="SAFETY_VETO",
            duration_ms=ex["duration_ms"],
            details={
                "attempted": False,
                "capability": ex.get("capability"),
                "mode": ex.get("mode"),
                "status": "BLOCKED",
                "reason": str(p3_sec["safety"].get("reason") or "Safety veto applied")
            }
        )
    elif ex["attempted"]:
        succ = bool(ex.get("result", {}).get("success")) if isinstance(ex.get("result"), dict) else (ex["status"] == "SUCCESS")
        recorder.record(
            phase="PHASE_4",
            component="executor",
            event="EXECUTION_COMPLETED",
            status=ex["status"],
            reason_code=ex.get("reason_code") or ex["status"],
            duration_ms=ex["duration_ms"],
            details={"attempted": True, "capability": ex.get("capability"), "mode": ex.get("mode"), "status": ex["status"], "success": succ}
        )
    else:
        recorder.record(
            phase="PHASE_4",
            component="executor",
            event="EXECUTION_SKIPPED",
            status=ex["status"],
            reason_code=ex.get("reason_code") or ex["status"],
            duration_ms=ex["duration_ms"],
            details={"attempted": False, "capability": ex.get("capability"), "mode": ex.get("mode"), "status": ex["status"]}
        )

    # 10f. AFTER_OBSERVATION_COMPLETED or AFTER_OBSERVATION_SKIPPED
    a_obs = p4_sec["after_observations"]
    recorder.record(
        phase="PHASE_4",
        component="observation",
        event="AFTER_OBSERVATION_COMPLETED" if a_obs["attempted"] else "AFTER_OBSERVATION_SKIPPED",
        status=a_obs["status"],
        reason_code=a_obs["reason_code"],
        duration_ms=a_obs["duration_ms"],
        details={"attempted": a_obs["attempted"], "status": a_obs["status"]}
    )

    # 10g. VERIFICATION_COMPLETED or VERIFICATION_SKIPPED
    ver = p4_sec["verification"]
    recorder.record(
        phase="PHASE_4",
        component="verifier",
        event="VERIFICATION_COMPLETED" if ver["attempted"] else "VERIFICATION_SKIPPED",
        status=ver["status"],
        reason_code=ver["reason_code"],
        duration_ms=ver["duration_ms"],
        details={"attempted": ver["attempted"], "status": ver["status"]}
    )

    # 10h. ROLLBACK_COMPLETED or ROLLBACK_SKIPPED
    rb = p4_sec["rollback"]
    recorder.record(
        phase="PHASE_4",
        component="rollback",
        event="ROLLBACK_COMPLETED" if rb["attempted"] else "ROLLBACK_SKIPPED",
        status=rb["status"],
        reason_code=rb["reason_code"],
        duration_ms=rb["duration_ms"],
        details={"attempted": rb["attempted"], "status": rb["status"]}
    )

    # 10i. CLEANUP_COMPLETED or CLEANUP_SKIPPED
    cl = p4_sec["cleanup"]
    recorder.record(
        phase="PHASE_4",
        component="cleanup",
        event="CLEANUP_COMPLETED" if cl["attempted"] else "CLEANUP_SKIPPED",
        status=cl["status"],
        reason_code=cl["reason_code"],
        duration_ms=cl["duration_ms"],
        details={"attempted": cl["attempted"], "status": cl["status"]}
    )

    # 11. Build/store learning episode and record learning event
    episode_db_stored = False
    learning_episode_obj = None
    try:
        learning_episode_obj = build_learning_episode(
            advisory=rl_advisory_obj,
            envelope=envelope,
            phase4_result=p4_context,
            run_id=problem_run_id
        )
        ep_store = EpisodeStore()
        if rl_advisory_obj:
            try:
                ep_store.save_advisory(rl_advisory_obj)
            except Exception:
                pass
        if learning_episode_obj:
            episode_db_stored = bool(ep_store.save_episode(learning_episode_obj))
    except Exception:
        episode_db_stored = False
        learning_episode_obj = None

    learning_sec = _build_learning_section(learning_episode_obj, simulated_flag, p4_sec, episode_stored=episode_db_stored)

    if learning_episode_obj is not None:
        recorder.record(
            phase="LEARNING",
            component="learning_engine",
            event="LEARNING_EPISODE_CREATED",
            status=learning_sec["status"],
            reason_code=learning_sec.get("eligibility_reason") or "NOT_ELIGIBLE",
            duration_ms=0.0,
            details={"status": learning_sec["status"], "stored": learning_sec.get("stored", False), "eligible": learning_sec.get("eligible", False)}
        )
    else:
        recorder.record(
            phase="LEARNING",
            component="learning_engine",
            event="LEARNING_EPISODE_SKIPPED",
            status="NOT_RUN",
            reason_code=learning_sec.get("eligibility_reason") or "NOT_ELIGIBLE",
            duration_ms=0.0,
            details={"status": "NOT_RUN", "stored": False, "eligible": False}
        )

    end_dt = datetime.now(timezone.utc)
    completed_at = end_dt.isoformat().replace("+00:00", "Z")
    duration_ms = (time.perf_counter() - start_time) * 1000.0

    # Build canonical report context
    run_sec = _build_run_section(verif_id, problem_run_id, started_at, completed_at, duration_ms, simulated_flag)
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

    # 12. Validate the final report context in-memory and record REPORT_VALIDATED
    temp_context = copy.deepcopy(canonical_context)
    temp_context["integrity"]["event_log_hash"] = "0" * 64
    temp_context["integrity"]["errors"] = []
    schema = load_report_schema()
    val = Draft7Validator(schema, format_checker=get_format_checker())
    val_errs = sorted(val.iter_errors(temp_context), key=lambda e: (list(e.path), e.message))
    if val_errs:
        err_msgs = [f"JSON Schema error at {e.json_path}: {e.message}" for e in val_errs]
        raise ReportContractError(f"In-memory canonical report validation failed: {err_msgs}")

    recorder.record(
        phase="REPORTING",
        component="report_validator",
        event="REPORT_VALIDATED",
        status="SUCCESS",
        reason_code="VALID",
        duration_ms=0.0,
        details={"schema_valid": True}
    )

    # 13. Record PROBLEM_RUN_COMPLETED last
    recorder.record(
        phase="REPORTING",
        component="coordinator",
        event="PROBLEM_RUN_COMPLETED",
        status=summary_sec["outcome"],
        reason_code=summary_sec["outcome"],
        duration_ms=duration_ms,
        details={
            "outcome": summary_sec["outcome"],
            "problem_resolved_in_sandbox": summary_sec.get("problem_resolved_in_sandbox", False),
            "execution_performed": summary_sec.get("execution_performed", False)
        }
    )

    # 14. Calculate event_log_hash from the final exact JSONL bytes
    final_events_hash = recorder.compute_hash()
    canonical_context["integrity"]["event_log_hash"] = final_events_hash
    canonical_context["integrity"]["errors"] = []

    final_val_errs = sorted(val.iter_errors(canonical_context), key=lambda e: (list(e.path), e.message))
    if final_val_errs:
        err_msgs = [f"JSON Schema error at {e.json_path}: {e.message}" for e in final_val_errs]
        raise ReportContractError(f"Final canonical report validation failed: {err_msgs}")

    # 15. Write phase34_events.jsonl
    events_path = recorder.write_atomic(reports_base_dir=reports_base_dir)

    # 16. Write phase34_report.json and phase34_report.md
    json_path, md_path = generate_phase34_report(canonical_context, reports_base_dir=reports_base_dir)

    final_outcome = summary_sec.get("outcome", "UNKNOWN")

    # Update stable runtime index pointers (runtime/report_index.json & runtime/latest_phase34_report.json)
    try:
        corr_id = raw_problem.get("correlation_id") or problem_run_id
        update_report_index(
            incident_id=case_id,
            correlation_id=corr_id,
            report_path=json_path,
            final_outcome=final_outcome,
            report_data=canonical_context,
            events_path=events_path
        )
    except Exception as idx_err:
        pass

    print(f"\n[MVP COORDINATOR] Incident [{case_id}] complete!")
    print(f"  - Final Outcome : {final_outcome}")
    print(f"  - JSON Report   : {json_path}")
    print(f"  - MD Report     : {md_path}")
    print(f"  - Events Log    : {events_path}\n")

    return {
        "incident_id": case_id,
        "case_id": case_id,
        "verification_run_id": verif_id,
        "problem_run_id": problem_run_id,
        "outcome": final_outcome,
        "json_report": json_path,
        "md_report": md_path,
        "events_report": events_path
    }


def main():
    parser = argparse.ArgumentParser(description="MVP Autonomous Remediation Pipeline Coordinator")
    parser.add_argument("--input", type=str, default=None, help="Path to input problem JSON file or problems directory")
    parser.add_argument("--all", action="store_true", help="Run all 22 cases in problems directory")
    parser.add_argument("--reports-dir", type=str, default=None, help="Custom base output directory for reports")
    parser.add_argument("--json-summary", action="store_true", help="Print structured machine-readable result JSON to stdout")

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

        if args.json_summary:
            print("\n[PIPELINE_RESULT_JSON]")
            print(json.dumps({"verification_run_id": verification_run_id, "results": results, "crashes": crashes}, indent=2))
            print("[/PIPELINE_RESULT_JSON]\n")

        if crashes:
            sys.exit(1)
        sys.exit(0)

    elif args.input:
        try:
            res = run_single_problem(args.input, reports_base_dir=args.reports_dir)
            if args.json_summary:
                print("\n[PIPELINE_RESULT_JSON]")
                print(json.dumps(res, indent=2))
                print("[/PIPELINE_RESULT_JSON]\n")
            sys.exit(0)
        except Exception as e:
            print(f"Pipeline Execution Error: {e}")
            sys.exit(1)
    else:
        default_file = os.path.join(problems_dir, "case_01.json")
        if not os.path.exists(default_file):
            default_file = os.path.join(BASE_DIR, "debate", "input", "case_01_semantic_consensus.json")
        res = run_single_problem(default_file, reports_base_dir=args.reports_dir)
        if args.json_summary:
            print("\n[PIPELINE_RESULT_JSON]")
            print(json.dumps(res, indent=2))
            print("[/PIPELINE_RESULT_JSON]\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
