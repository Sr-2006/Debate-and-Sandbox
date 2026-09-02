#!/usr/bin/env python3
"""
shadow_sandbox/run_pipeline.py

Pipeline Orchestrator for Shadow Sandboxing Subsystem.
Sequences Layer 2 (faults/), Layer 3 (remediation/), and Layer 4 (reports/) with state machines,
atomic deduplication claims, and truthful outcome reporting.
"""

import os
import sys
import time
import json
import glob
import argparse
from typing import Dict, Any, Optional, Set

from contracts.canonical_json import compute_payload_hash
from contracts.reason_codes import ReasonCode, TerminalState
from shadow_sandbox.persistence import SandboxPersistence
from shadow_sandbox.state_machine import ExecutionStateMachine
from shadow_sandbox.faults.fault_agent import FaultSelectionAgent
from shadow_sandbox.faults.fault_injector import recover_all, log_fault_event
from shadow_sandbox.remediation.execution_harness import ExecutionHarness
from shadow_sandbox.remediation.remediation_agent import BoundedRemediationAgent
from shadow_sandbox.reports.report_generator import generate_report


def process_incident(incident_file: str, settle_wait_s: float = 1.0) -> Optional[str]:
    """Processes a single incident JSON file through the state-machine execution pipeline."""
    if not os.path.exists(incident_file):
        print(f"[ORCHESTRATOR] File not found: {incident_file}")
        return None

    with open(incident_file, "r", encoding="utf-8") as f:
        incident = json.load(f)

    incident_id = incident.get("incident_id", os.path.splitext(os.path.basename(incident_file))[0])
    problem_text = incident.get("problem", incident.get("problem_summary", ""))

    # 1. State Machine initialization & Validation BEFORE Idempotency Claim & BEFORE Fault Setup
    payload_hash = incident.get("payload_hash") or compute_payload_hash(incident)
    sm = ExecutionStateMachine(incident_id, payload_hash)

    if not sm.transition_to("VALIDATING", ReasonCode.DIAGNOSED, "Validating payload format and schema v2"):
        print(f"[ORCHESTRATOR] [{incident_id}] Invalid initial state transition to VALIDATING")
        return None

    # Lossless V2 Envelope check
    v2_envelope = incident.get("raw_v2_envelope") or incident
    if not isinstance(v2_envelope, dict) or v2_envelope.get("schema_version") != "2.0":
        sm.transition_to(TerminalState.BLOCKED_LEGACY_PAYLOAD.value, ReasonCode.BLOCKED_LEGACY_PAYLOAD, "Unstructured legacy payload blocked; schema v2 required")
        summary = sm.get_summary()
        outcome = {
            "incident_id": incident_id,
            "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "gate_decision": "BLOCKED_LEGACY_PAYLOAD",
            "confidence_score": 0.0,
            "human_intervention_required": True,
            "message": "Execution blocked: Legacy string payloads are blocked; envelope v2 structured intent required.",
            "fault_cleared": False,
            "state_machine_history": summary["history"]
        }
        return generate_report(outcome)

    from contracts.validation import validate_envelope
    is_valid, errs, val_reason = validate_envelope(v2_envelope)
    if not is_valid:
        sm.transition_to(val_reason.value, val_reason, "; ".join(errs))
        summary = sm.get_summary()
        outcome = {
            "incident_id": incident_id,
            "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "gate_decision": val_reason.value,
            "confidence_score": 0.0,
            "human_intervention_required": True,
            "message": f"Payload validation failed: {'; '.join(errs)}",
            "fault_cleared": False,
            "state_machine_history": summary["history"]
        }
        return generate_report(outcome)

    # 2. Atomic Deduplication via SQLite Inbox Claim (AFTER validation passes)
    persistence = SandboxPersistence()
    if not persistence.claim_payload(payload_hash, incident_id):
        print(f"[ORCHESTRATOR] [{incident_id}] Duplicate payload detected ({payload_hash[:8]}...). Skipping execution.")
        sm.transition_to(TerminalState.DUPLICATE_IGNORED.value, ReasonCode.DUPLICATE_IGNORED, "Duplicate payload delivery ignored")
        duplicate_outcome = {
            "incident_id": incident_id,
            "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "gate_decision": "DUPLICATE_IGNORED",
            "human_intervention_required": False,
            "message": "Duplicate payload delivery ignored by inbox deduplication constraint.",
            "fault_cleared": False,
            "state_machine_history": sm.get_summary()["history"]
        }
        return generate_report(duplicate_outcome)

    if not sm.transition_to("CLAIMED", ReasonCode.DIAGNOSED, "Payload claimed in inbox persistence"):
        print(f"[ORCHESTRATOR] [{incident_id}] Invalid state transition to CLAIMED")
        return None

    # Extract structured intent and target_ref directly
    intents = v2_envelope.get("intents", [])
    first_intent = intents[0] if intents else {}
    target_ref = first_intent.get("target_ref") or v2_envelope.get("target_ref") or {}
    target = target_ref.get("canonical_name")

    from contracts.validation import get_capabilities
    capabilities = get_capabilities()
    cap_meta = capabilities.get(first_intent.get("intent_type"), {})

    proposal = {
        "intent_type": first_intent.get("intent_type"),
        "mode": first_intent.get("mode"),
        "target": target,
        "target_kind": target_ref.get("kind", "container"),
        "target_ref": target_ref,
        "parameters": first_intent.get("parameters", {}),
        "evidence_refs": first_intent.get("evidence_refs", []),
        "requires_human_approval": first_intent.get("requires_human_approval", v2_envelope.get("safety_violation", False)),
        "qualification_run": incident.get("qualification_run") or v2_envelope.get("qualification_run"),
        "executor_name": first_intent.get("executor_name") or cap_meta.get("executor"),
        "verifier_name": first_intent.get("verifier_name") or cap_meta.get("verifier"),
        "confidence": v2_envelope.get("phase3_confidence", {}).get("score", 0.85) if isinstance(v2_envelope.get("phase3_confidence"), dict) else 0.85
    }

    remediation_agent = BoundedRemediationAgent()

    # 3. Preflight Authorization Check BEFORE Fault Injection
    harness = ExecutionHarness(agent=remediation_agent, persistence=persistence)
    preflight_ok, preflight_res = harness.preflight(proposal, problem_text, sm)


    if not preflight_ok:
        # Preflight failed (unattested target, unmapped capability, safety veto, insufficient history, missing params, etc.)
        # Write report and stop execution WITHOUT injecting any fault!
        summary = sm.get_summary()
        outcome = {
            "incident_id": incident_id,
            "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "gate_decision": summary["terminal_state"],
            "confidence_score": preflight_res.get("confidence_eval", {}).get("execution_confidence", 0.0),
            "human_intervention_required": summary["terminal_state"] != "VERIFIED_RECOVERED",
            "message": preflight_res.get("detail"),
            "agent_proposal": proposal,
            "execution_result": preflight_res,
            "fault_cleared": False,
            "state_machine_history": summary["history"]
        }
        report_path = generate_report(outcome)
        print(f"[ORCHESTRATOR] [{incident_id}] Preflight blocked ({summary['terminal_state']}). Report: {report_path}")
        return report_path

    # 4. Fault Injection Setup (runs ONLY AFTER preflight authorization passes!)
    fault_agent = FaultSelectionAgent()
    shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"
    primitive, params = fault_agent.infer_fault_primitive(problem_text, "", target or "")

    fault_setup_ok = True
    fault_error_msg = ""

    cleanup_ok = True
    cleanup_msg = ""

    try:
        if primitive and target:
            try:
                recover_all(shadow_target)
                before_state = fault_agent.execute_fault_primitive(shadow_target, primitive, params)
                log_fault_event(incident_id, primitive, shadow_target, params, before_state, active=True)
            except Exception as e:
                fault_setup_ok = False
                fault_error_msg = f"Fault setup failed for primitive '{primitive}': {str(e)}"
                print(f"[ORCHESTRATOR] [{incident_id}] {fault_error_msg}")

        if not fault_setup_ok:
            sm.transition_to("SETTING_UP_FAULT", ReasonCode.DIAGNOSED, "Applying fault primitive")
            sm.transition_to("FAULT_SETUP_FAILED", ReasonCode.FAULT_SETUP_FAILED, fault_error_msg)
            summary = sm.get_summary()
            outcome = {
                "incident_id": incident_id,
                "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "gate_decision": "FAULT_SETUP_FAILED",
                "confidence_score": 0.0,
                "human_intervention_required": True,
                "message": fault_error_msg,
                "agent_proposal": proposal,
                "execution_result": None,
                "fault_cleared": False,
                "state_machine_history": summary["history"]
            }
            return generate_report(outcome)

        # 5. Execute Authorized Remediation & Verification
        exec_res = harness.execute_authorized(preflight_res, sm)

    finally:
        # Immutable cleanup in finally block
        if primitive and target:
            try:
                recover_all(shadow_target)
            except Exception as e:
                cleanup_ok = False
                cleanup_msg = f"Cleanup failed for target '{shadow_target}': {str(e)}"

    # 6. Write Final Outcome Report
    summary = sm.get_summary()
    gate_decision = summary["terminal_state"]

    if not cleanup_ok and gate_decision == "VERIFIED_RECOVERED":
        gate_decision = "CLEANUP_FAILED"
        sm.transition_to("CLEANUP_FAILED", ReasonCode.CLEANUP_FAILED, cleanup_msg)

    outcome = {
        "incident_id": incident_id,
        "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gate_decision": gate_decision,
        "confidence_score": exec_res.get("confidence_eval", {}).get("execution_confidence", 0.0),
        "human_intervention_required": gate_decision != "VERIFIED_RECOVERED",
        "message": exec_res.get("detail") if cleanup_ok else cleanup_msg,
        "agent_proposal": proposal,
        "execution_result": exec_res,
        "fault_cleared": exec_res.get("fault_cleared", False) and cleanup_ok,
        "state_machine_history": sm.get_summary()["history"]
    }

    report_path = generate_report(outcome)
    print(f"[ORCHESTRATOR] [{incident_id}] Processed ({gate_decision}). Report: {report_path}")
    return report_path





def run_batch_mode(input_dir: str, settle_wait_s: float = 1.0):
    pattern = os.path.join(input_dir, "*.json")
    files = sorted(glob.glob(pattern))

    if not files:
        print(f"[ORCHESTRATOR] No fix JSON files found matching pattern '{pattern}'.")
        return

    print(f"[ORCHESTRATOR] Starting Batch Mode run over {len(files)} incident file(s)...")
    for idx, filepath in enumerate(files, 1):
        print(f"\n--- Processing Incident {idx}/{len(files)}: {os.path.basename(filepath)} ---")
        process_incident(filepath, settle_wait_s=settle_wait_s)

    print(f"\n[ORCHESTRATOR] Batch Mode run completed successfully.")


def run_watch_mode(input_dir: str, poll_interval_s: float = 2.0, settle_wait_s: float = 1.0):
    print(f"[ORCHESTRATOR] Starting Watch Mode monitoring '{input_dir}' (polling every {poll_interval_s}s)...")
    processed_files: Set[str] = set()
    pattern = os.path.join(input_dir, "*.json")
    
    try:
        while True:
            current_files = set(glob.glob(pattern))
            new_files = sorted(list(current_files - processed_files))

            for filepath in new_files:
                if not filepath.endswith(".tmp"):
                    print(f"\n[WATCH] New incident detected: {os.path.basename(filepath)}")
                    process_incident(filepath, settle_wait_s=settle_wait_s)
                    processed_files.add(filepath)

            time.sleep(poll_interval_s)
    except KeyboardInterrupt:
        print("\n[ORCHESTRATOR] Watch Mode stopped by user.")


def main():
    parser = argparse.ArgumentParser(description="Shadow Sandbox Pipeline Orchestrator")
    parser.add_argument("input_path", nargs="?", default=None, help="Path to incident JSON file or input directory")
    parser.add_argument("--mode", choices=["batch", "watch"], default="batch", help="Pipeline execution mode (batch | watch)")
    parser.add_argument("--settle-wait", type=float, default=1.0, help="Settle wait duration in seconds")

    args = parser.parse_args()
    default_dir = os.path.join(os.path.dirname(__file__), "sample_inputs")
    
    if args.input_path and os.path.isfile(args.input_path):
        process_incident(args.input_path, settle_wait_s=args.settle_wait)
    else:
        target_dir = args.input_path if (args.input_path and os.path.isdir(args.input_path)) else default_dir
        if args.mode == "watch":
            run_watch_mode(target_dir, settle_wait_s=args.settle_wait)
        else:
            run_batch_mode(target_dir, settle_wait_s=args.settle_wait)


if __name__ == "__main__":
    main()