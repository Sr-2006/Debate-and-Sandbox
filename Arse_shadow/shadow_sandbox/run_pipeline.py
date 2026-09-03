#!/usr/bin/env python3
"""
shadow_sandbox/run_pipeline.py

Simplified 7-Step Phase 4 Shadow Sandbox Pipeline Orchestrator:
1. RECEIVED
2. VALIDATED
3. OBSERVED_BEFORE
4. EXECUTED_OR_BLOCKED
5. OBSERVED_AFTER
6. VERIFIED_OR_ROLLED_BACK
7. REPORTED
"""

import os
import sys
import time
import json
import glob
import argparse
from typing import Dict, Any, Optional

from contracts.canonical_json import compute_payload_hash
from contracts.reason_codes import ReasonCode, TerminalState
from contracts.validation import validate_envelope, get_capabilities, is_mvp_supported
from shadow_sandbox.persistence import SandboxPersistence
from shadow_sandbox.state_machine import ExecutionStateMachine
from shadow_sandbox.remediation.executors.docker_executor import DockerExecutor, MockDockerExecutor
from shadow_sandbox.remediation.executors.postgres_executor import PostgresExecutor, MockPostgresExecutor
from shadow_sandbox.remediation.executors.redis_executor import RedisExecutor, MockRedisExecutor
from shadow_sandbox.remediation.verifiers.service_health import ServiceHealthVerifier, MockVerifier

from shadow_sandbox.remediation.verifiers.postgres_verifier import PostgresVerifier
from shadow_sandbox.remediation.verifiers.redis_verifier import RedisVerifier
from shadow_sandbox.faults.fault_agent import FaultSelectionAgent
from shadow_sandbox.faults.fault_injector import recover_all, log_fault_event


def get_executor(executor_name: str, is_simulated: bool = False):
    if is_simulated:
        if executor_name == "postgres_executor":
            return MockPostgresExecutor()
        elif executor_name == "redis_executor":
            return MockRedisExecutor()
        else:
            return MockDockerExecutor()
    if executor_name == "postgres_executor":
        return PostgresExecutor()
    elif executor_name == "redis_executor":
        return RedisExecutor()
    else:
        return DockerExecutor()


def get_verifier(verifier_name: str, is_simulated: bool = False):
    if is_simulated:
        return MockVerifier()
    if verifier_name == "postgres_verifier":
        return PostgresVerifier()
    elif verifier_name == "redis_verifier":
        return RedisVerifier()
    else:
        return ServiceHealthVerifier()


def check_attestation(
    target_name: str,
    target_kind: str = "container",
    target_ref: Optional[Dict[str, Any]] = None,
    blocked: bool = False,
    is_simulated: bool = False
) -> Dict[str, Any]:
    if blocked or not target_name or target_name in ["None", "unknown", "unknown-service", "shadow-None", "shadow-unknown-service", ""]:
        return {
            "attempted": False,
            "attested": False,
            "reason": "Execution blocked before target attestation"
        }

    canonical = (target_ref.get("canonical_name") if target_ref else None) or target_name
    shadow_target = canonical if canonical.startswith("shadow-") else f"shadow-{canonical}"

    if is_simulated:
        return {
            "attempted": True,
            "attested": True,
            "simulated": True,
            "target": shadow_target,
            "container_id": "c1234567890a",
            "status": "running"
        }

    from shadow_sandbox.attestation import attest_shadow_environment

    passed, reason_code, reason_msg = attest_shadow_environment(
        target_name=canonical,
        target_kind=target_kind,
        target_ref=target_ref
    )

    reason_str = reason_code.value if hasattr(reason_code, "value") else str(reason_code)

    if passed:
        return {
            "attempted": True,
            "attested": True,
            "target": shadow_target,
            "status": "running",
            "reason_code": reason_str,
            "reason": reason_msg
        }
    else:
        return {
            "attempted": True,
            "attested": False,
            "target": shadow_target,
            "reason_code": reason_str,
            "reason": reason_msg
        }


def run_phase4_pipeline(v2_envelope: Dict[str, Any], fault_spec: Optional[Dict[str, Any]] = None, is_simulated: bool = False) -> Dict[str, Any]:
    """
    Executes Phase 4 shadow sandbox in 7 explicit steps and returns structured phase_4 context block.
    """
    start_time = time.perf_counter()
    simulated_flag = is_simulated or bool(v2_envelope.get("simulated")) or bool(os.environ.get("DEBATE_MOCK_LLM") == "1")

    incident_id = v2_envelope.get("incident_id", "case_unknown")
    payload_hash = v2_envelope.get("payload_hash") or compute_payload_hash(v2_envelope)

    sm = ExecutionStateMachine(incident_id, payload_hash)

    # 1. Step 1: RECEIVED (Recorded on ExecutionStateMachine initialization)

    # 2. Step 2: VALIDATED
    is_valid, errs, val_reason = validate_envelope(v2_envelope)
    if not is_valid:
        sm.transition_to("VALIDATION_FAILED", val_reason, "; ".join(errs))
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "status": "VALIDATION_FAILED",
            "exact_input": v2_envelope,
            "target": v2_envelope.get("target_ref", {}),
            "attestation": {"attempted": False, "attested": False, "reason": "Execution blocked before target attestation"},
            "before_observations": {},
            "fault_setup": {"injected": False},
            "execution": {"capability": "unknown", "parameters": {}, "result": {}, "duration_ms": 0},
            "after_observations": {},
            "verification": {"passed": False},
            "rollback": {"attempted": False, "result": None},
            "cleanup": {"completed": True},
            "state_history": sm.get_summary()["history"],
            "duration_ms": duration_ms
        }

    sm.transition_to("VALIDATED", ReasonCode.DIAGNOSED, "Validated v2 envelope schema and catalog entry")

    intents = v2_envelope.get("intents", [])
    first_intent = intents[0] if intents else {}
    intent_type = first_intent.get("intent_type", "NO_SUPPORTED_ACTION")
    mode = first_intent.get("mode", "OBSERVE")
    target_ref = first_intent.get("target_ref") or v2_envelope.get("target_ref") or {}
    canonical_target = target_ref.get("canonical_name")
    shadow_target = f"shadow-{canonical_target}" if canonical_target else None
    parameters = first_intent.get("parameters", {})
    requires_human_approval = bool(first_intent.get("requires_human_approval", False) or v2_envelope.get("safety_violation", False))

    capabilities = get_capabilities()
    mapping_valid = intent_type in capabilities and intent_type != "NO_SUPPORTED_ACTION"
    cap_meta = capabilities.get(intent_type, {})
    is_supported = is_mvp_supported(intent_type)

    diag_conf = v2_envelope.get("phase3_confidence", {}).get("score", 0.0)

    # Check routing conditions
    if intent_type == "NO_SUPPORTED_ACTION" or not mapping_valid:
        sm.transition_to("BLOCKED_UNKNOWN_CAPABILITY", ReasonCode.BLOCKED_UNKNOWN_CAPABILITY, f"Unmapped intent '{intent_type}'")
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "status": "NO_SUPPORTED_ACTION",
            "exact_input": v2_envelope,
            "target": target_ref,
            "attestation": {"attempted": False, "attested": False, "reason": "Execution blocked before target attestation"},
            "before_observations": {},
            "fault_setup": {"injected": False},
            "execution": {"capability": intent_type, "parameters": parameters, "result": {"success": False, "reason": "No supported action"}, "duration_ms": 0},
            "after_observations": {},
            "verification": {"passed": False},
            "rollback": {"attempted": False, "result": None},
            "cleanup": {"completed": True},
            "state_history": sm.get_summary()["history"],
            "duration_ms": duration_ms
        }

    if diag_conf is not None and diag_conf < 0.50:
        # Run read-only observation only
        sm.transition_to("OBSERVED_BEFORE", ReasonCode.DIAGNOSED, "Running read-only observation for low confidence case")
        doc_exec = get_executor("docker_executor", is_simulated=simulated_flag)
        obs_target = shadow_target or "shadow-container"
        obs_res = doc_exec.execute(obs_target, "observe.logs.search", {"max_lines": 50})
        obs_attestation = check_attestation(canonical_target or obs_target, target_kind=target_ref.get("kind", "container") if target_ref else "container", target_ref=target_ref, blocked=False, is_simulated=simulated_flag)
        sm.transition_to("REPORTED", ReasonCode.DIAGNOSED, "Read-only observation complete")
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "status": "READ_ONLY_OBSERVED",
            "exact_input": v2_envelope,
            "target": target_ref,
            "attestation": obs_attestation,
            "before_observations": {"obs": obs_res},
            "fault_setup": {"injected": False},
            "execution": {"capability": "observe.logs.search", "parameters": {"max_lines": 50}, "result": obs_res, "duration_ms": 0},
            "after_observations": {"obs": obs_res},
            "verification": {"passed": bool(obs_res.get("success", False))},
            "rollback": {"attempted": False, "result": None},
            "cleanup": {"completed": True},
            "state_history": sm.get_summary()["history"],
            "duration_ms": duration_ms
        }

    if mode == "MUTATE_HIGH_RISK" or requires_human_approval:
        sm.transition_to("BLOCKED_SAFETY_VIOLATION", ReasonCode.BLOCKED_SAFETY, f"High risk action {intent_type} requires human approval")

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "status": "HUMAN_REVIEW_REQUIRED",
            "exact_input": v2_envelope,
            "target": target_ref,
            "attestation": {"attempted": False, "attested": False, "reason": "Execution blocked before target attestation"},
            "before_observations": {},
            "fault_setup": {"injected": False},
            "execution": {"capability": intent_type, "parameters": parameters, "result": {"success": False, "reason": "High risk action requires human review"}, "duration_ms": 0},
            "after_observations": {},
            "verification": {"passed": False},
            "rollback": {"attempted": False, "result": None},
            "cleanup": {"completed": True},
            "state_history": sm.get_summary()["history"],
            "duration_ms": duration_ms
        }

    if not is_supported:
        sm.transition_to("UNSUPPORTED_IN_MVP", ReasonCode.BLOCKED_UNKNOWN_CAPABILITY, f"Capability '{intent_type}' is not supported in MVP")
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "status": "UNSUPPORTED_IN_MVP",
            "exact_input": v2_envelope,
            "target": target_ref,
            "attestation": {"attempted": False, "attested": False, "reason": "Execution blocked before target attestation"},
            "before_observations": {},
            "fault_setup": {"injected": False},
            "execution": {
                "capability": intent_type,
                "parameters": parameters,
                "result": {"success": False, "reason": f"Capability '{intent_type}' has mvp_supported: false. Implementation deferred."},
                "duration_ms": 0
            },
            "after_observations": {},
            "verification": {"passed": False, "detail": "Unsupported executor in MVP"},
            "rollback": {"attempted": False, "result": None},
            "cleanup": {"completed": True},
            "state_history": sm.get_summary()["history"],
            "duration_ms": duration_ms
        }

    # Perform real target attestation check
    target_kind_name = target_ref.get("kind", "container") if target_ref else "container"
    attestation = check_attestation(canonical_target or shadow_target, target_kind=target_kind_name, target_ref=target_ref, blocked=False, is_simulated=simulated_flag)
    if not attestation.get("attested"):
        sm.transition_to("ATTESTATION_FAILED", ReasonCode.ATTESTATION_FAILED, f"Target attestation failed for {shadow_target}")
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "status": "ATTESTATION_FAILED",
            "exact_input": v2_envelope,
            "target": target_ref,
            "attestation": attestation,
            "before_observations": {},
            "fault_setup": {"injected": False},
            "execution": {"capability": intent_type, "parameters": parameters, "result": {"success": False, "reason": "Attestation failed"}, "duration_ms": 0},
            "after_observations": {},
            "verification": {"passed": False},
            "rollback": {"attempted": False, "result": None},
            "cleanup": {"completed": True},
            "state_history": sm.get_summary()["history"],
            "duration_ms": duration_ms
        }

    # 3. Step 3: OBSERVED_BEFORE
    sm.transition_to("OBSERVED_BEFORE", ReasonCode.DIAGNOSED, "Capturing target pre-state before mutation")
    executor_name = cap_meta.get("executor", "docker_executor")
    verifier_name = cap_meta.get("verifier", "service_health")
    executor = get_executor(executor_name, is_simulated=simulated_flag)
    verifier = get_verifier(verifier_name, is_simulated=simulated_flag)


    # Optional Fault Injection if fault_spec provided
    fault_info = {"injected": False}
    if fault_spec and isinstance(fault_spec, dict) and fault_spec.get("fault_type"):
        fault_agent = FaultSelectionAgent()
        primitive = fault_spec.get("fault_type")
        f_params = fault_spec.get("parameters", {})
        try:
            recover_all(shadow_target)
            before_state = fault_agent.execute_fault_primitive(shadow_target, primitive, f_params)
            log_fault_event(incident_id, primitive, shadow_target, f_params, before_state, active=True)
            fault_info = {"injected": True, "primitive": primitive, "parameters": f_params, "initial_state": before_state}
        except Exception as e:
            fault_info = {"injected": False, "error": str(e)}

    # Capture before_observations
    before_obs = {}
    pre_state_val = None
    try:
        if intent_type == "postgres.setting.update":
            s_name = parameters.get("setting_name")
            r = executor.execute(shadow_target, "postgres.setting.read", {"setting_name": s_name})
            before_obs = r
            if r.get("success"):
                pre_state_val = r.get("output", "").replace("SUCCESS: ", "").strip()
        elif intent_type == "redis.eviction_policy.update":
            r = executor.execute(shadow_target, "redis.eviction_policy.read", {})
            before_obs = r
            if r.get("success"):
                pre_state_val = r.get("output", "").replace("SUCCESS: ", "").strip()
        elif intent_type == "container.restart":
            before_obs = executor.inspect_container(shadow_target)
            if before_obs.get("success"):
                pre_state_val = before_obs
        elif intent_type == "observe.logs.search":
            before_obs = executor.execute(shadow_target, "observe.logs.search", parameters)
            pre_state_val = before_obs
    except Exception as e:
        before_obs = {"error": str(e)}

    # If before-state cannot be read for mutating capability, do NOT mutate!
    if mode != "OBSERVE" and pre_state_val is None:
        sm.transition_to("PRECONDITION_FAILED", ReasonCode.PRECONDITION_FAILED, f"Unable to read genuine pre-state for {intent_type}; mutation aborted")
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "status": "PRECONDITION_FAILED",
            "exact_input": v2_envelope,
            "target": target_ref,
            "attestation": attestation,
            "before_observations": before_obs,
            "fault_setup": fault_info,
            "execution": {"capability": intent_type, "parameters": parameters, "result": {"success": False, "reason": "Pre-state read failed"}, "duration_ms": 0},
            "after_observations": {},
            "verification": {"passed": False},
            "rollback": {"attempted": False, "result": None},
            "cleanup": {"completed": True},
            "state_history": sm.get_summary()["history"],
            "duration_ms": duration_ms
        }

    # 4. Step 4: EXECUTED_OR_BLOCKED
    sm.transition_to("EXECUTED_OR_BLOCKED", ReasonCode.DIAGNOSED, f"Executing {intent_type}")
    exec_start = time.perf_counter()
    exec_res = executor.execute(shadow_target, intent_type, parameters)
    exec_duration_ms = int((time.perf_counter() - exec_start) * 1000)

    # 5. Step 5: OBSERVED_AFTER
    sm.transition_to("OBSERVED_AFTER", ReasonCode.DIAGNOSED, "Capturing target post-state")
    after_obs = {}
    try:
        if intent_type == "postgres.setting.update":
            s_name = parameters.get("setting_name")
            after_obs = executor.execute(shadow_target, "postgres.setting.read", {"setting_name": s_name})
        elif intent_type == "redis.eviction_policy.update":
            after_obs = executor.execute(shadow_target, "redis.eviction_policy.read", {})
        elif intent_type == "container.restart":
            after_obs = executor.inspect_container(shadow_target)
        elif intent_type == "observe.logs.search":
            after_obs = exec_res
    except Exception as e:
        after_obs = {"error": str(e)}

    # 6. Step 6: VERIFIED_OR_ROLLED_BACK
    sm.transition_to("VERIFIED_OR_ROLLED_BACK", ReasonCode.DIAGNOSED, "Verifying postconditions and executing rollback if needed")
    ver_res = verifier.verify(shadow_target, intent_type, parameters, exec_res)
    passed = bool(ver_res.get("passed", False))

    rollback_info = {"attempted": False, "result": None}
    final_status = "SIMULATION_VERIFIED" if simulated_flag else "SANDBOX_VERIFIED"

    if passed:
        final_status = "SIMULATION_VERIFIED" if simulated_flag else "SANDBOX_VERIFIED"

    else:
        # Perform rollback to captured pre-state
        rollback_info["attempted"] = True
        rb_exec = {}
        try:
            if intent_type == "postgres.setting.update" and isinstance(pre_state_val, str):
                s_name = parameters.get("setting_name")
                rb_exec = executor.execute(shadow_target, intent_type, {"setting_name": s_name, "value": pre_state_val})
            elif intent_type == "redis.eviction_policy.update" and isinstance(pre_state_val, str):
                rb_exec = executor.execute(shadow_target, intent_type, {"policy": pre_state_val})
            elif intent_type == "container.restart":
                rb_exec = executor.execute(shadow_target, "container.restart", {})
            else:
                rb_exec = {"success": False, "reason": "No captured pre-state value available for rollback"}
        except Exception as e:
            rb_exec = {"success": False, "error": str(e)}

        rollback_info["result"] = rb_exec

        # Re-read post-rollback state and verify restoration against captured pre-state
        post_rb_obs = {}
        restored = False
        try:
            if intent_type == "postgres.setting.update" and isinstance(pre_state_val, str):
                s_name = parameters.get("setting_name")
                post_rb_obs = executor.execute(shadow_target, "postgres.setting.read", {"setting_name": s_name})
                val = post_rb_obs.get("output", "").replace("SUCCESS: ", "").strip()
                restored = (val == pre_state_val)
            elif intent_type == "redis.eviction_policy.update" and isinstance(pre_state_val, str):
                post_rb_obs = executor.execute(shadow_target, "redis.eviction_policy.read", {})
                val = post_rb_obs.get("output", "").replace("SUCCESS: ", "").strip()
                restored = (val == pre_state_val)
            elif intent_type == "container.restart":
                post_rb_obs = executor.inspect_container(shadow_target)
                restored = rb_exec.get("success", False) and post_rb_obs.get("success", False)
        except Exception:
            restored = False

        if rb_exec.get("success") and restored:
            final_status = "SANDBOX_FAILED_ROLLED_BACK"
        else:
            final_status = "SANDBOX_FAILED_ROLLBACK_FAILED"

    # 7. Step 7: REPORTED
    reported_reason = ReasonCode.VERIFIED_RECOVERED if final_status in ["SANDBOX_VERIFIED", "SIMULATION_VERIFIED"] else ReasonCode.VERIFICATION_FAILED_ROLLED_BACK
    sm.transition_to("REPORTED", reported_reason, f"Phase 4 completed with status {final_status}")


    # Cleanup fault if injected
    if fault_info.get("injected"):
        try:
            recover_all(shadow_target)
        except Exception:
            pass

    duration_ms = int((time.perf_counter() - start_time) * 1000)

    return {
        "status": final_status,
        "exact_input": v2_envelope,
        "target": target_ref,
        "attestation": attestation,
        "before_observations": before_obs,
        "fault_setup": fault_info,
        "execution": {
            "capability": intent_type,
            "parameters": parameters,
            "result": exec_res,
            "duration_ms": exec_duration_ms
        },
        "after_observations": after_obs,
        "verification": ver_res,
        "rollback": rollback_info,
        "cleanup": {"completed": True},
        "state_history": sm.get_summary()["history"],
        "duration_ms": duration_ms
    }



def process_incident(incident_file: str, settle_wait_s: float = 1.0) -> Optional[str]:
    """Legacy incident runner."""
    if not os.path.exists(incident_file):
        return None
    with open(incident_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    v2_env = data.get("raw_v2_envelope") or data
    res = run_phase4_pipeline(v2_env, fault_spec=data.get("fault_spec"))
    return res.get("status")