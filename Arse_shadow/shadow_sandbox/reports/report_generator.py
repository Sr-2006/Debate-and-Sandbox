#!/usr/bin/env python3
"""
shadow_sandbox/reports/report_generator.py

Canonical Phase 3+4 Report Producer.
Generates validated JSON context reports and deterministic Markdown renderings under:
reports/<verification_run_id>/cases/<case_id>/phase34_report.json
reports/<verification_run_id>/cases/<case_id>/phase34_report.md
"""

import os
import sys
import json
import copy
import uuid
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Tuple, Optional
from jsonschema import Draft7Validator, FormatChecker

REPORTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "reports"))
CONTRACTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "contracts"))
SCHEMA_FILE = os.path.join(CONTRACTS_DIR, "phase34_report_v1.schema.json")

EMPTY_EVENT_LOG_HASH = hashlib.sha256(b"").hexdigest()

class ReportContractError(ValueError):
    """Raised when a report context fails contract schema validation."""
    pass


class ReportAtomicityError(ReportContractError):
    """Raised when atomic report pair replacement or restoration fails."""
    def __init__(self, message: str, backup_paths: Optional[list[str]] = None):
        super().__init__(message)
        self.backup_paths = backup_paths or []


def get_format_checker() -> FormatChecker:
    fc = FormatChecker()
    @fc.checks("date-time")
    def check_datetime(val):
        if not isinstance(val, str):
            return True
        try:
            if "T" not in val:
                return False
            datetime.fromisoformat(val.replace("Z", "+00:00"))
            return True
        except Exception:
            return False
    return fc


def load_report_schema() -> dict:
    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_report_hash(report: dict) -> str:
    """
    Calculates deterministic SHA-256 hash of report.
    Algorithm:
    1. Deep-copy the report.
    2. Set integrity.report_hash to an empty string.
    3. Serialize using json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    4. Encode as UTF-8.
    5. Return SHA-256 hexadecimal digest.
    """
    copied = copy.deepcopy(report)
    if "integrity" not in copied or not isinstance(copied["integrity"], dict):
        copied["integrity"] = {}
    copied["integrity"]["report_hash"] = ""

    serialized = json.dumps(
        copied,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _render_stage_section(title: str, stage: dict) -> list[str]:
    lines = [f"## {title}"]
    if not isinstance(stage, dict):
        lines.append("- **Status**: `NOT_RUN`")
        return lines

    for k in ["status", "attempted", "reason_code", "reason", "duration_ms"]:
        if k in stage:
            lines.append(f"- **{k.replace('_', ' ').title()}**: `{stage[k]}`")

    if "capability" in stage:
        lines.append(f"- **Capability**: `{stage['capability']}`")
    if "mode" in stage:
        lines.append(f"- **Mode**: `{stage['mode']}`")

    data = stage.get("data")
    if data is not None:
        lines.append("```json")
        lines.append(json.dumps(data, indent=2, ensure_ascii=False))
        lines.append("```")

    parameters = stage.get("parameters")
    if parameters is not None:
        lines.append("- **Parameters**:")
        lines.append("```json")
        lines.append(json.dumps(parameters, indent=2, ensure_ascii=False))
        lines.append("```")

    result = stage.get("result")
    if result is not None:
        lines.append("- **Result**:")
        lines.append("```json")
        lines.append(json.dumps(result, indent=2, ensure_ascii=False))
        lines.append("```")

    return lines


def _render_phase34_markdown(report: dict) -> str:
    """Renders deterministic Markdown from validated report dict."""
    summary = report.get("final_summary", {})
    run = report.get("run", {})
    prob = report.get("problem", {})
    p3 = report.get("phase_3", {})
    handoff = report.get("phase_3_to_4_handoff", {})
    rl = report.get("rl_advisory", {})
    p4 = report.get("phase_4", {})
    learning = report.get("learning", {})
    integrity = report.get("integrity", {})

    lines = [
        "# Phase 3–4 Problem Summary",
        "## Final Summary",
        f"- **Outcome**: `{summary.get('outcome', 'NOT_RUN')}`",
        f"- **Problem Resolved in Sandbox**: `{summary.get('problem_resolved_in_sandbox', False)}`",
        f"- **Execution Performed**: `{summary.get('execution_performed', False)}`",
        f"- **Human Intervention Required**: `{summary.get('human_intervention_required', False)}`",
        f"- **Recommended Next Action**: `{summary.get('recommended_next_action', 'REQUIRE_HUMAN_REVIEW')}`",
        f"- **What Happened**: {summary.get('what_happened', '')}",
        f"- **Why It Happened**: {summary.get('why_it_happened', '')}",
        f"- **Safety Result**: `{summary.get('safety_result', 'UNKNOWN')}`",
        f"- **Confidence Result**: `{summary.get('confidence_result', 'UNKNOWN')}`",
        "- **Limitations**:",
    ]

    limitations = summary.get("limitations", [])
    if isinstance(limitations, list):
        for lim in limitations:
            lines.append(f"  - {lim}")
    else:
        lines.append(f"  - {limitations}")

    lines.extend([
        "",
        "## Run Identity",
        f"- **Verification Run ID**: `{run.get('verification_run_id', '')}`",
        f"- **Problem Run ID**: `{run.get('problem_run_id', '')}`",
        f"- **Commit SHA**: `{run.get('commit_sha', '')}`",
        f"- **Started At**: `{run.get('started_at', '')}`",
        f"- **Completed At**: `{run.get('completed_at', '')}`",
        f"- **Duration (ms)**: `{run.get('duration_ms', 0)}`",
        f"- **Execution Mode**: `{run.get('execution_mode', '')}`",
        f"- **Mock LLM**: `{run.get('mock_llm', False)}`",
        f"- **RL Operating Mode**: `{run.get('rl_operating_mode', '')}`",
        f"- **Laptop 1 Transport**: `{run.get('laptop1_transport', None)}`",
        "",
        "## Problem Input",
        f"- **Case ID**: `{prob.get('case_id', '')}`",
        f"- **Source File**: `{prob.get('source_file', '')}`",
        f"- **Input Hash**: `{prob.get('input_hash', '')}`",
        f"- **Severity**: `{prob.get('severity', '')}`",
        f"- **Target**: `{prob.get('target', {})}`",
        f"- **Expected Behavior**: {prob.get('expected_behavior', '')}",
        "- **Raw Input**:",
        "```json",
        json.dumps(prob.get("raw_input", {}), indent=2, ensure_ascii=False),
        "```",
        "- **Normalized Incident**:",
        "```json",
        json.dumps(prob.get("normalized_incident", {}), indent=2, ensure_ascii=False),
        "```",
        "",
        "## Phase 3 Debate",
        f"- **Status**: `{p3.get('status', 'NOT_RUN')}`",
        f"- **Started At**: `{p3.get('started_at', '')}`",
        f"- **Completed At**: `{p3.get('completed_at', '')}`",
        f"- **Duration (ms)**: `{p3.get('duration_ms', 0)}`",
        f"- **Agreement**: `{p3.get('agreement', None)}`",
        f"- **Orchestrator Decision**: `{p3.get('orchestrator_decision', '')}`",
        "- **Selected Intent**:",
        "```json",
        json.dumps(p3.get("selected_intent"), indent=2, ensure_ascii=False),
        "```",
        f"- **Reason Codes**: `{p3.get('reason_codes', [])}`",
        "- **Agents**:",
    ])

    agents = p3.get("agents", {})
    for agent_name in ["optimist", "critic", "fact_checker"]:
        ag = agents.get(agent_name, {})
        lines.append(f"  ### Agent: {agent_name}")
        lines.append(f"  - **Status**: `{ag.get('status', 'NOT_RUN')}`")
        lines.append(f"  - **Valid**: `{ag.get('valid', False)}`")
        lines.append(f"  - **Latency (ms)**: `{ag.get('latency_ms', 0)}`")
        lines.append(f"  - **Error**: `{ag.get('error', None)}`")

    conf = p3.get("confidence", {})
    lines.extend([
        "",
        "## Phase 3 Confidence and Safety",
        f"- **Score**: `{conf.get('score', None)}`",
        f"- **Threshold**: `{conf.get('threshold', None)}`",
        f"- **Uncertainty**: `{conf.get('uncertainty', None)}`",
        f"- **Calibration Status**: `{conf.get('calibration_status', 'UNAVAILABLE')}`",
        f"- **Evidence Count**: `{conf.get('evidence_count', 0)}`",
        f"- **Component Agreement**: `{conf.get('component_agreement', None)}`",
        f"- **Evidence Grounding**: `{conf.get('evidence_grounding', None)}`",
        f"- **Veto Applied**: `{conf.get('veto_applied', False)}`",
        f"- **Veto Cap**: `{conf.get('veto_cap', None)}`",
        f"- **Reason Codes**: `{conf.get('reason_codes', [])}`",
        "- **Safety**:",
        "```json",
        json.dumps(p3.get("safety"), indent=2, ensure_ascii=False),
        "```",
        "",
        "## Phase 3 to Phase 4 Handoff",
        f"- **Status**: `{handoff.get('status', '')}`",
        f"- **Schema Valid**: `{handoff.get('schema_valid', False)}`",
        f"- **Validation Errors**: `{handoff.get('validation_errors', [])}`",
        f"- **Payload Hash**: `{handoff.get('payload_hash', '')}`",
        f"- **Capability Mapped**: `{handoff.get('capability_mapped', False)}`",
        f"- **MVP Supported**: `{handoff.get('mvp_supported', False)}`",
        f"- **Target Resolved**: `{handoff.get('target_resolved', False)}`",
        "- **Exact Envelope**:",
        "```json",
        json.dumps(handoff.get("exact_envelope"), indent=2, ensure_ascii=False),
        "```",
        "",
        "## RL Advisory",
        f"- **Status**: `{rl.get('status', '')}`",
        f"- **Operating Mode**: `{rl.get('operating_mode', '')}`",
        f"- **Policy Version**: `{rl.get('policy_version', '')}`",
        f"- **Model Version**: `{rl.get('model_version', '')}`",
        f"- **Recommendation**: `{rl.get('recommendation', '')}`",
        f"- **Allowed Actions**: `{rl.get('allowed_actions', [])}`",
        f"- **Action Scores**: `{rl.get('action_scores', {})}`",
        f"- **Uncertainty**: `{rl.get('uncertainty', 0.0)}`",
        f"- **Sample Size**: `{rl.get('sample_size', 0)}`",
        f"- **Cold Start**: `{rl.get('cold_start', True)}`",
        f"- **Influence Allowed**: `{rl.get('influence_allowed', False)}`",
        f"- **Reason Codes**: `{rl.get('reason_codes', [])}`",
        f"- **Feature Hash**: `{rl.get('feature_hash', '')}`",
        f"- **Latency (ms)**: `{rl.get('latency_ms', 0)}`",
        ""
    ])

    # Phase 4 stages
    lines.extend(_render_stage_section("Phase 4 Attestation", p4.get("attestation", {})))
    lines.append("")
    lines.extend(_render_stage_section("Before Observations", p4.get("before_observations", {})))
    lines.append("")
    lines.extend(_render_stage_section("Execution", p4.get("execution", {})))
    lines.append("")
    lines.extend(_render_stage_section("After Observations", p4.get("after_observations", {})))
    lines.append("")
    lines.extend(_render_stage_section("Verification", p4.get("verification", {})))
    lines.append("")
    lines.extend(_render_stage_section("Rollback", p4.get("rollback", {})))
    lines.append("")
    lines.extend(_render_stage_section("Cleanup", p4.get("cleanup", {})))
    lines.append("")

    lines.extend([
        "## Learning",
        f"- **Status**: `{learning.get('status', '')}`",
        f"- **Episode ID**: `{learning.get('episode_id', '')}`",
        f"- **Eligible**: `{learning.get('eligible', False)}`",
        f"- **Eligibility Reason**: `{learning.get('eligibility_reason', '')}`",
        f"- **Behavior Action**: `{learning.get('behavior_action', '')}`",
        f"- **Reward**: `{learning.get('reward', None)}`",
        f"- **Sample Weight**: `{learning.get('sample_weight', 0.0)}`",
        f"- **Feature Hash**: `{learning.get('feature_hash', '')}`",
        f"- **Stored**: `{learning.get('stored', False)}`",
        "",
        "## Integrity",
        f"- **Report Schema Valid**: `{integrity.get('report_schema_valid', False)}`",
        f"- **Input Hash**: `{integrity.get('input_hash', '')}`",
        f"- **Payload Hash**: `{integrity.get('payload_hash', '')}`",
        f"- **Event Log Hash**: `{integrity.get('event_log_hash', '')}`",
        f"- **Report Hash**: `{integrity.get('report_hash', '')}`",
        f"- **Errors**: `{integrity.get('errors', [])}`",
    ])

    return "\n".join(lines)


def generate_phase34_report(
    context: dict,
    reports_base_dir: str | None = None,
) -> tuple[str, str]:
    """
    Public Canonical Phase 3+4 Report Producer.
    Validates context against phase34_report_v1.schema.json, computes report hash,
    renders Markdown, and atomically writes both JSON and Markdown as a safe pair.
    """
    report = copy.deepcopy(context)

    # Initialize integrity placeholders for schema validation
    if "integrity" not in report or not isinstance(report["integrity"], dict):
        report["integrity"] = {}
    report["integrity"]["report_hash"] = "0" * 64
    report["integrity"]["report_schema_valid"] = True
    if not report["integrity"].get("event_log_hash"):
        report["integrity"]["event_log_hash"] = EMPTY_EVENT_LOG_HASH

    errors_list = list(report["integrity"].get("errors", []))
    if report["integrity"].get("event_log_hash") == EMPTY_EVENT_LOG_HASH:
        if "PHASE34_EVENT_LOG_NOT_EMITTED_PHASE3" not in errors_list:
            errors_list.append("PHASE34_EVENT_LOG_NOT_EMITTED_PHASE3")
    else:
        if "PHASE34_EVENT_LOG_NOT_EMITTED_PHASE3" in errors_list:
            errors_list.remove("PHASE34_EVENT_LOG_NOT_EMITTED_PHASE3")
    report["integrity"]["errors"] = errors_list

    # Initial Schema Validation
    schema = load_report_schema()
    validator = Draft7Validator(schema, format_checker=get_format_checker())
    validation_errors = sorted(validator.iter_errors(report), key=lambda e: (list(e.path), e.message))
    if validation_errors:
        err_msgs = [f"JSON Schema error at {e.json_path}: {e.message}" for e in validation_errors]
        raise ReportContractError(f"Report contract validation failed: {err_msgs}")

    # Compute actual report hash and update
    actual_hash = compute_report_hash(report)
    report["integrity"]["report_hash"] = actual_hash

    # Final Schema Validation
    final_errors = sorted(validator.iter_errors(report), key=lambda e: (list(e.path), e.message))
    if final_errors:
        err_msgs = [f"JSON Schema error at {e.json_path}: {e.message}" for e in final_errors]
        raise ReportContractError(f"Final report contract validation failed: {err_msgs}")

    # Render Markdown payload BEFORE touching file system
    md_content = _render_phase34_markdown(report)

    # Output pathing
    base_dir = reports_base_dir or REPORTS_DIR
    verification_run_id = report.get("run", {}).get("verification_run_id", "unknown_verify")
    case_id = report.get("problem", {}).get("case_id", "unknown_case")

    dest_dir = os.path.join(base_dir, verification_run_id, "cases", case_id)
    os.makedirs(dest_dir, exist_ok=True)

    dest_json = os.path.join(dest_dir, "phase34_report.json")
    dest_md = os.path.join(dest_dir, "phase34_report.md")

    pid_str = f"{os.getpid()}_{uuid.uuid4().hex}"
    tmp_json = os.path.join(dest_dir, f".phase34_report.json.tmp.{pid_str}")
    tmp_md = os.path.join(dest_dir, f".phase34_report.md.tmp.{pid_str}")
    bak_json = os.path.join(dest_dir, f".phase34_report.json.bak.{pid_str}")
    bak_md = os.path.join(dest_dir, f".phase34_report.md.bak.{pid_str}")

    # Step 1: Write both temporary files first with flush & fsync
    try:
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        with open(tmp_md, "w", encoding="utf-8") as f:
            f.write(md_content)
            f.flush()
            os.fsync(f.fileno())
    except Exception as write_err:
        for t_file in [tmp_json, tmp_md]:
            if os.path.exists(t_file):
                try:
                    os.remove(t_file)
                except Exception:
                    pass
        raise write_err

    # Step 2: Backup existing files if present
    backed_up_json = False
    backed_up_md = False
    try:
        if os.path.exists(dest_json):
            os.replace(dest_json, bak_json)
            backed_up_json = True
        if os.path.exists(dest_md):
            os.replace(dest_md, bak_md)
            backed_up_md = True
    except Exception as bak_err:
        restoration_err = None
        if backed_up_json and os.path.exists(bak_json):
            try:
                os.replace(bak_json, dest_json)
            except Exception as r_e:
                restoration_err = r_e
        for t_file in [tmp_json, tmp_md]:
            if os.path.exists(t_file):
                try:
                    os.remove(t_file)
                except Exception:
                    pass
        if restoration_err is not None:
            raise ReportAtomicityError(
                f"Backup phase failed and restoration also failed: {restoration_err}",
                backup_paths=[bak_json] if os.path.exists(bak_json) else []
            ) from restoration_err
        raise bak_err

    # Step 3: Replace destination files atomically as a pair
    replaced_json = False
    replaced_md = False
    try:
        os.replace(tmp_json, dest_json)
        replaced_json = True
        os.replace(tmp_md, dest_md)
        replaced_md = True
    except Exception as replace_err:
        restoration_err = None

        if replaced_json and os.path.exists(dest_json):
            try:
                os.remove(dest_json)
            except Exception as r_e:
                restoration_err = r_e

        if replaced_md and os.path.exists(dest_md):
            try:
                os.remove(dest_md)
            except Exception as r_e:
                restoration_err = r_e

        retained_baks = []
        if backed_up_json and os.path.exists(bak_json):
            try:
                os.replace(bak_json, dest_json)
            except Exception as r_e:
                restoration_err = r_e
                retained_baks.append(bak_json)

        if backed_up_md and os.path.exists(bak_md):
            try:
                os.replace(bak_md, dest_md)
            except Exception as r_e:
                restoration_err = r_e
                retained_baks.append(bak_md)

        # Remove only temporary .tmp files
        for t_file in [tmp_json, tmp_md]:
            if os.path.exists(t_file):
                try:
                    os.remove(t_file)
                except Exception:
                    pass

        if restoration_err is not None:
            raise ReportAtomicityError(
                f"Report pair replacement failed and restoration also failed: {restoration_err}",
                backup_paths=retained_baks
            ) from restoration_err

        raise replace_err

    # Step 4: Cleanup backups after successful replacement
    for b_file in [bak_json, bak_md]:
        if os.path.exists(b_file):
            try:
                os.remove(b_file)
            except Exception:
                pass

    return dest_json, dest_md
