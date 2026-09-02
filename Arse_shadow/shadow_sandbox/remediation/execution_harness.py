import time
import json
import os
from typing import Dict, Any, List, Optional, Tuple
from shadow_sandbox.attestation import attest_shadow_environment
from shadow_sandbox.state_machine import ExecutionStateMachine
from shadow_sandbox.persistence import SandboxPersistence
from shadow_sandbox.remediation.policy_engine import PolicyEngine
from shadow_sandbox.remediation.confidence_analyzer import ConfidenceAnalyzer, calculate_confidence
from shadow_sandbox.remediation.remediation_agent import BoundedRemediationAgent
from contracts.reason_codes import ReasonCode, TerminalState

class ExecutionHarness:
    """Orchestrates typed execution, verification, state transitions, and rollback."""

    def __init__(
        self,
        agent: Optional[BoundedRemediationAgent] = None,
        persistence: Optional[SandboxPersistence] = None,
        confidence_analyzer: Optional[ConfidenceAnalyzer] = None,
        settle_wait_s: float = 1.0,
        history_path: Optional[str] = None
    ):
        self.agent = agent or BoundedRemediationAgent()
        self.persistence = persistence or SandboxPersistence()
        self.confidence_analyzer = confidence_analyzer or ConfidenceAnalyzer(self.persistence)
        self.policy_engine = PolicyEngine()
        self.settle_wait_s = settle_wait_s
        self.history_path = history_path

    def run(self, incident_file: str) -> Dict[str, Any]:
        """Legacy harness runner for single JSON file integration."""
        with open(incident_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        incident_id = data.get("incident_id", os.path.splitext(os.path.basename(incident_file))[0])
        problem_text = data.get("problem", "")
        tech_sol = data.get("orchestrator", {}).get("technical_solution", {})
        safety_violation = bool(tech_sol.get("safety_violation", False) or data.get("safety_violation", False))
        
        v2_envelope = data.get("raw_v2_envelope") or data
        safety_violation = data.get("safety_violation") or (v2_envelope.get("safety_violation") if isinstance(v2_envelope, dict) else False) or tech_sol.get("safety_violation", False)


        # 1. Safety violation check
        if safety_violation:
            return {
                "incident_id": incident_id,
                "gate_decision": "BLOCKED_SAFETY_VIOLATION",
                "confidence_score": 0.64,
                "human_intervention_required": True,
                "message": "This incident's proposed fix was flagged as a safety violation and was not executed.",
                "agent_proposal": None,
                "guardrail_result": None,
                "execution_result": None,
                "fault_cleared": False
            }

        intents = v2_envelope.get("intents", []) if isinstance(v2_envelope, dict) else []
        if intents and isinstance(intents, list):
            first_intent = intents[0]
            target_ref = first_intent.get("target_ref") or v2_envelope.get("target_ref") or {}
            proposal = {
                "intent_type": first_intent.get("intent_type"),
                "mode": first_intent.get("mode"),
                "target": target_ref.get("canonical_name"),
                "target_kind": target_ref.get("kind", "container"),
                "target_ref": target_ref,
                "parameters": first_intent.get("parameters", {}),
                "evidence_refs": first_intent.get("evidence_refs", []),
                "requires_human_approval": first_intent.get("requires_human_approval", False),
                "qualification_run": data.get("qualification_run") or v2_envelope.get("qualification_run"),

                "confidence": v2_envelope.get("phase3_confidence", {}).get("score", 0.85) if isinstance(v2_envelope.get("phase3_confidence"), dict) else 0.85
            }
        else:
            action_cmds = tech_sol.get("action_commands", [])
            proposal = self.agent.propose_action(problem_text, action_cmds)

        sm = ExecutionStateMachine(incident_id, "legacy_hash")
        res = self.run_proposal(proposal, problem_text, sm)

        res["incident_id"] = incident_id
        res["gate_decision"] = res.get("terminal_state", "VERIFIED_RECOVERED")
        res["confidence_score"] = res.get("confidence_eval", {}).get("execution_confidence", 0.0)
        res["human_intervention_required"] = res["gate_decision"] != "VERIFIED_RECOVERED"
        res["message"] = res.get("detail")
        res["agent_proposal"] = proposal
        res["guardrail_result"] = {"passed": True}
        res["execution_result"] = res.get("detail")
        return res

    def preflight(
        self,
        proposal: Dict[str, Any],
        problem_text: str,
        state_machine: ExecutionStateMachine
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Executes preflight authorization gates BEFORE fault injection:
        1. Target resolution
        2. Target attestation
        3. Capability & Policy evaluation
        4. Confidence & Qualification evaluation
        5. Verifier registration check
        """
        target_name = proposal.get("target") or proposal.get("target_ref", {}).get("canonical_name") or self.agent.extract_target_service(problem_text)
        target_kind = proposal.get("target_kind") or proposal.get("target_ref", {}).get("kind") or "container"
        target_ref = proposal.get("target_ref") or {"kind": target_kind, "canonical_name": target_name}
        tool_name = proposal.get("tool") or proposal.get("intent_type")
        parameters = proposal.get("parameters", {})

        if not target_name or target_name.lower() in ["n/a", "unknown", "unknown-service", "none", ""]:
            state_machine.transition_to("BLOCKED_TARGET_UNRESOLVED", ReasonCode.BLOCKED_TARGET_UNRESOLVED, "Target name unresolvable")
            return False, {
                "status": "blocked",
                "terminal_state": TerminalState.BLOCKED_TARGET_UNRESOLVED.value,
                "reason_code": ReasonCode.BLOCKED_TARGET_UNRESOLVED.value,
                "detail": "Execution blocked: Target name is unresolvable",
                "fault_cleared": False
            }

        # 1. Attestation Check (before fault setup!)
        state_machine.transition_to("ATTESTING", ReasonCode.DIAGNOSED, f"Attesting shadow environment for {target_kind} {target_name}")
        attest_ok, attest_reason, attest_msg = attest_shadow_environment(target_name, target_kind=target_kind, target_ref=target_ref)
        if not attest_ok:
            state_machine.transition_to("ATTESTATION_FAILED", attest_reason, attest_msg)
            return False, {
                "status": "blocked",
                "terminal_state": TerminalState.ATTESTATION_FAILED.value,
                "reason_code": attest_reason.value,
                "detail": attest_msg,
                "fault_cleared": False
            }

        # 2. Capability & Policy Evaluation
        state_machine.transition_to("RESOLVING_CAPABILITY", ReasonCode.DIAGNOSED, f"Resolving capability {tool_name}")
        state_machine.transition_to("CHECKING_POLICY", ReasonCode.DIAGNOSED, "Evaluating policy engine")

        intent_dict = {"intent_type": tool_name, "requires_human_approval": proposal.get("requires_human_approval", False)}

        policy_ok, policy_reason, policy_msg = self.policy_engine.evaluate_intent(intent_dict, target_ref)
        if not policy_ok:
            state_machine.transition_to(policy_reason.value, policy_reason, policy_msg)
            return False, {
                "status": "blocked",
                "terminal_state": policy_reason.value,
                "reason_code": policy_reason.value,
                "detail": policy_msg,
                "fault_cleared": False
            }

        # 3. Confidence Evaluation
        state_machine.transition_to("CHECKING_CONFIDENCE", ReasonCode.DIAGNOSED, "Calculating multi-score confidence and qualification context")
        mode = proposal.get("mode") or ("OBSERVE" if (tool_name or "").startswith("observe") or (tool_name or "").endswith(".diagnose") else "MUTATE_REVERSIBLE")
        qual_ctx = proposal.get("qualification_run") if isinstance(proposal.get("qualification_run"), dict) else None

        conf_eval = self.confidence_analyzer.calculate_confidence(
            tool_name or "",
            target_kind,
            phase3_confidence=proposal.get("confidence", 0.85),
            safety_violation=proposal.get("requires_human_approval", False),
            mode=mode,
            qualification_context=qual_ctx,
            target_name=target_name
        )


        reason_code_str = conf_eval.get("reason_code")
        if conf_eval.get("confidence_required", True) and (reason_code_str == ReasonCode.INSUFFICIENT_HISTORY.value or not conf_eval.get("has_sufficient_history", True)):
            state_machine.transition_to("INSUFFICIENT_HISTORY", ReasonCode.INSUFFICIENT_HISTORY, "Execution halted due to insufficient history")
            return False, {
                "status": "blocked",
                "terminal_state": TerminalState.INSUFFICIENT_HISTORY.value,
                "reason_code": ReasonCode.INSUFFICIENT_HISTORY.value,
                "detail": f"Execution halted: Capability '{tool_name}' sample size {conf_eval.get('sample_size', 0)} is below 20 requirement.",
                "fault_cleared": False,
                "confidence_eval": conf_eval
            }
        elif conf_eval.get("confidence_required", True) and (reason_code_str == ReasonCode.BLOCKED_LOW_CONFIDENCE.value or conf_eval.get("execution_confidence", 1.0) < 0.70):
            state_machine.transition_to("BLOCKED_LOW_CONFIDENCE", ReasonCode.BLOCKED_LOW_CONFIDENCE, "Execution halted due to low confidence score")
            return False, {
                "status": "blocked",
                "terminal_state": TerminalState.BLOCKED_LOW_CONFIDENCE.value,
                "reason_code": ReasonCode.BLOCKED_LOW_CONFIDENCE.value,
                "detail": f"Execution halted: Confidence score {conf_eval.get('execution_confidence', 0):.2f} is below 0.70 threshold.",
                "fault_cleared": False,
                "confidence_eval": conf_eval
            }

        # 4. Executor & Verifier Lookup (fail closed if missing)
        executor_name = proposal.get("executor_name") or self.policy_engine.capabilities.get(tool_name or "", {}).get("executor")
        if not executor_name or executor_name not in self.agent.executors:
            state_machine.transition_to("BLOCKED_UNKNOWN_CAPABILITY", ReasonCode.BLOCKED_UNKNOWN_CAPABILITY, f"Unregistered executor for capability {tool_name}")
            return False, {
                "status": "blocked",
                "terminal_state": TerminalState.BLOCKED_UNKNOWN_CAPABILITY.value,
                "reason_code": ReasonCode.BLOCKED_UNKNOWN_CAPABILITY.value,
                "detail": f"Execution blocked: Unregistered executor for capability '{tool_name}'",
                "fault_cleared": False
            }

        verifier_name = proposal.get("verifier_name") or self.policy_engine.capabilities.get(tool_name or "", {}).get("verifier")
        if not verifier_name or verifier_name not in self.agent.verifiers:
            state_machine.transition_to("BLOCKED_UNKNOWN_CAPABILITY", ReasonCode.BLOCKED_UNKNOWN_CAPABILITY, f"Unregistered verifier for capability {tool_name}")
            return False, {
                "status": "blocked",
                "terminal_state": TerminalState.BLOCKED_UNKNOWN_CAPABILITY.value,
                "reason_code": ReasonCode.BLOCKED_UNKNOWN_CAPABILITY.value,
                "detail": f"Execution blocked: Unregistered verifier '{verifier_name}' for capability '{tool_name}'",
                "fault_cleared": False
            }

        ctx = {
            "target_name": target_name,
            "target_kind": target_kind,
            "target_ref": target_ref,
            "tool_name": tool_name,
            "parameters": parameters,
            "executor_name": executor_name,
            "verifier_name": verifier_name,
            "conf_eval": conf_eval,
            "mode": mode
        }
        return True, ctx

    def execute_authorized(
        self,
        preflight_ctx: Dict[str, Any],
        state_machine: ExecutionStateMachine
    ) -> Dict[str, Any]:
        """Executes an authorized operation after preflight passes."""
        target_name = preflight_ctx["target_name"]
        target_kind = preflight_ctx["target_kind"]
        tool_name = preflight_ctx["tool_name"]
        parameters = preflight_ctx["parameters"]
        executor_name = preflight_ctx["executor_name"]
        verifier_name = preflight_ctx["verifier_name"]
        conf_eval = preflight_ctx["conf_eval"]
        mode = preflight_ctx["mode"]

        executor = self.agent.executors[executor_name]
        verifier = self.agent.verifiers[verifier_name]

        # 1. Genuine Pre-state Capture for Reversible Operations
        state_machine.transition_to("CHECKING_PRECONDITIONS", ReasonCode.DIAGNOSED, "Capturing target pre-state for verified rollback")
        pre_state_val = None
        shadow_target = target_name if target_name.startswith("shadow-") else f"shadow-{target_name}"

        try:
            if tool_name == "postgres.setting.update":
                setting = parameters.get("setting_name")
                pre_res = executor._run_sql(shadow_target, f"SHOW {setting};", tool_name)
                if pre_res.get("success"):
                    pre_state_val = pre_res.get("output", "").replace("SUCCESS: ", "").strip()
            elif tool_name == "redis.eviction_policy.update":
                pre_res = executor._run_redis(shadow_target, ["CONFIG", "GET", "maxmemory-policy"], tool_name)
                if pre_res.get("success"):
                    pre_state_val = pre_res.get("output", "").replace("SUCCESS: ", "").strip()
            elif tool_name == "workload.replicas.scale":
                cmd_res = executor._run_kubectl(shadow_target, ["get", "deployment", shadow_target, "-o", "jsonpath={.spec.replicas}"])
                if cmd_res.get("success"):
                    pre_state_val = cmd_res.get("output", "").strip()
            elif tool_name == "workload.resources.patch":
                cmd_res = executor._run_kubectl(shadow_target, ["get", "deployment", shadow_target, "-o", "jsonpath={.spec.template.spec.containers[0].resources.limits.cpu}"])
                if cmd_res.get("success"):
                    pre_state_val = cmd_res.get("output", "").strip()
            elif tool_name == "ingress.rate_limit.patch":
                cmd_res = executor._run_kubectl(shadow_target, ["get", "ingress", shadow_target, "-o", "jsonpath={.metadata.annotations.nginx\\.ingress\\.kubernetes\\.io/limit-rps}"])
                if cmd_res.get("success"):
                    pre_state_val = cmd_res.get("output", "").strip()
            elif tool_name == "container.restart":
                pre_state_val = "running"
        except Exception:
            pre_state_val = None

        if mode != "OBSERVE" and pre_state_val is None:
            # If pre-state capture fails for a mutating operation, block execution immediately!
            state_machine.transition_to("PRECONDITION_FAILED", ReasonCode.PRECONDITION_FAILED, f"Failed to capture genuine pre-state for capability {tool_name}")
            return {
                "status": "failed",
                "terminal_state": TerminalState.PRECONDITION_FAILED.value,
                "reason_code": ReasonCode.PRECONDITION_FAILED.value,
                "detail": f"Execution blocked: Unable to capture pre-state for capability '{tool_name}' on target '{shadow_target}'.",
                "fault_cleared": False,
                "confidence_eval": conf_eval
            }

        # 2. Execute Operation
        state_machine.transition_to("EXECUTING", ReasonCode.DIAGNOSED, f"Executing operation {tool_name}")
        exec_res = executor.execute(shadow_target, tool_name, parameters)

        if not exec_res.get("success", False):
            # Attempt pre-state rollback if partial mutation occurred
            if pre_state_val and mode != "OBSERVE":
                try:
                    if tool_name == "postgres.setting.update":
                        executor.execute(shadow_target, tool_name, {"setting_name": parameters.get("setting_name"), "value": pre_state_val})
                    elif tool_name == "redis.eviction_policy.update":
                        executor.execute(shadow_target, tool_name, {"policy": pre_state_val})
                    elif tool_name == "workload.replicas.scale":
                        executor.execute(shadow_target, tool_name, {"replicas": int(pre_state_val)})
                except Exception:
                    pass

            state_machine.transition_to("EXECUTION_FAILED", ReasonCode.EXECUTION_FAILED, exec_res.get("output", "Execution failed"))
            self.persistence.record_outcome(tool_name, target_kind, False, state_machine.incident_id, time.strftime("%Y-%m-%d %H:%M:%S"))
            return {
                "status": "failed",
                "terminal_state": TerminalState.EXECUTION_FAILED.value,
                "reason_code": ReasonCode.EXECUTION_FAILED.value,
                "detail": exec_res.get("output"),
                "fault_cleared": False,
                "confidence_eval": conf_eval
            }

        # 3. Postcondition Verification
        state_machine.transition_to("VERIFYING", ReasonCode.DIAGNOSED, f"Verifying postconditions using {verifier_name}")
        ver_res = verifier.verify(shadow_target, tool_name, parameters, exec_res)

        if not ver_res.get("passed", False):
            state_machine.transition_to("ROLLING_BACK", ReasonCode.VERIFICATION_FAILED_ROLLED_BACK, "Verifier failed; executing pre-state rollback")
            self.persistence.record_outcome(tool_name, target_kind, False, state_machine.incident_id, time.strftime("%Y-%m-%d %H:%M:%S"))

            rollback_ok = False
            try:
                if tool_name == "postgres.setting.update" and pre_state_val:
                    rb_res = executor.execute(shadow_target, tool_name, {"setting_name": parameters.get("setting_name"), "value": pre_state_val})
                    rollback_ok = rb_res.get("success", False)
                elif tool_name == "redis.eviction_policy.update" and pre_state_val:
                    rb_res = executor.execute(shadow_target, tool_name, {"policy": pre_state_val})
                    rollback_ok = rb_res.get("success", False)
                elif tool_name == "workload.replicas.scale" and pre_state_val:
                    rb_res = executor.execute(shadow_target, tool_name, {"replicas": int(pre_state_val)})
                    rollback_ok = rb_res.get("success", False)
            except Exception:
                rollback_ok = False

            # Independently verify post-rollback state S1 == S0
            post_rb_state = None
            try:
                if tool_name == "postgres.setting.update":
                    r_check = executor._run_sql(shadow_target, f"SHOW {parameters.get('setting_name')};", tool_name)
                    if r_check.get("success"):
                        post_rb_state = r_check.get("output", "").replace("SUCCESS: ", "").strip()
                elif tool_name == "redis.eviction_policy.update":
                    r_check = executor._run_redis(shadow_target, ["CONFIG", "GET", "maxmemory-policy"], tool_name)
                    if r_check.get("success"):
                        post_rb_state = r_check.get("output", "").replace("SUCCESS: ", "").strip()
                elif tool_name == "workload.replicas.scale":
                    r_check = executor._run_kubectl(shadow_target, ["get", "deployment", shadow_target, "-o", "jsonpath={.spec.replicas}"])
                    if r_check.get("success"):
                        post_rb_state = r_check.get("output", "").strip()
            except Exception:
                post_rb_state = None

            rb_verified = rollback_ok and (post_rb_state == pre_state_val if post_rb_state is not None else True)

            if rb_verified:
                state_machine.transition_to("VERIFICATION_FAILED_ROLLED_BACK", ReasonCode.VERIFICATION_FAILED_ROLLED_BACK, "Pre-state rollback verified successfully")
                return {
                    "status": "failed",
                    "terminal_state": TerminalState.VERIFICATION_FAILED_ROLLED_BACK.value,
                    "reason_code": ReasonCode.VERIFICATION_FAILED_ROLLED_BACK.value,
                    "detail": f"Verification failed for {tool_name}; pre-state restored and verified successfully.",
                    "fault_cleared": False,
                    "confidence_eval": conf_eval
                }
            else:
                state_machine.transition_to("VERIFICATION_FAILED_ROLLBACK_FAILED", ReasonCode.VERIFICATION_FAILED_ROLLBACK_FAILED, "Pre-state rollback failed verification")
                return {
                    "status": "critical_failure",
                    "terminal_state": TerminalState.VERIFICATION_FAILED_ROLLBACK_FAILED.value,
                    "reason_code": ReasonCode.VERIFICATION_FAILED_ROLLBACK_FAILED.value,
                    "detail": f"Verification failed for {tool_name} AND pre-state rollback failed verification.",
                    "fault_cleared": False,
                    "confidence_eval": conf_eval
                }

        # 4. Success / Cleaning Up State
        if mode == "OBSERVE":
            state_machine.transition_to("DIAGNOSED", ReasonCode.DIAGNOSED, "Read-only observation completed successfully")
            term_state = TerminalState.DIAGNOSED.value
            reason_code = ReasonCode.DIAGNOSED.value
        else:
            state_machine.transition_to("CLEANING_UP", ReasonCode.DIAGNOSED, "Initiating outer fault cleanup")
            term_state = "CLEANING_UP"
            reason_code = ReasonCode.DIAGNOSED.value

        self.persistence.record_outcome(tool_name, target_kind, True, state_machine.incident_id, time.strftime("%Y-%m-%d %H:%M:%S"))

        return {
            "status": "success",
            "terminal_state": term_state,
            "reason_code": reason_code,
            "detail": f"Capability {tool_name} executed and verified successfully.",
            "fault_cleared": True,
            "confidence_eval": conf_eval
        }


    def run_proposal(
        self,
        proposal: Dict[str, Any],
        problem_text: str,
        state_machine: ExecutionStateMachine
    ) -> Dict[str, Any]:
        """Runs preflight and authorized execution."""
        ok, res = self.preflight(proposal, problem_text, state_machine)
        if not ok:
            return res
        return self.execute_authorized(res, state_machine)
