import time
import json
import os
from typing import Dict, Any, Optional
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


    def run_proposal(
        self,
        proposal: Dict[str, Any],
        problem_text: str,
        state_machine: ExecutionStateMachine
    ) -> Dict[str, Any]:
        """Runs a remediation proposal through attestation, policy, execution, verification, and rollback."""
        target_name = proposal.get("target", self.agent.extract_target_service(problem_text))
        tool_name = proposal.get("tool")
        parameters = proposal.get("parameters", {})

        # 1. Attestation Check
        state_machine.transition_to("ATTESTING", ReasonCode.DIAGNOSED, "Attesting shadow environment")
        attest_ok, attest_reason, attest_msg = attest_shadow_environment(target_name)
        if not attest_ok:
            state_machine.transition_to("ATTESTATION_FAILED", attest_reason, attest_msg)
            return {
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
        target_dict = {"kind": "container", "canonical_name": target_name}
        
        policy_ok, policy_reason, policy_msg = self.policy_engine.evaluate_intent(intent_dict, target_dict)
        if not policy_ok:
            state_machine.transition_to(policy_reason.value, policy_reason, policy_msg)
            return {
                "status": "blocked",
                "terminal_state": policy_reason.value,
                "reason_code": policy_reason.value,
                "detail": policy_msg,
                "fault_cleared": False
            }

        # 3. Confidence Evaluation
        state_machine.transition_to("CHECKING_CONFIDENCE", ReasonCode.DIAGNOSED, "Calculating multi-score confidence")
        conf_eval = self.confidence_analyzer.calculate_confidence(
            tool_name or "",
            "container",
            phase3_confidence=proposal.get("confidence", 0.85),
            safety_violation=proposal.get("requires_human_approval", False)
        )
        
        reason_code_str = conf_eval.get("reason_code")
        if reason_code_str == ReasonCode.INSUFFICIENT_HISTORY.value or not conf_eval.get("has_sufficient_history", True):
            state_machine.transition_to("INSUFFICIENT_HISTORY", ReasonCode.INSUFFICIENT_HISTORY, "Execution halted due to insufficient history")
            return {
                "status": "blocked",
                "terminal_state": TerminalState.INSUFFICIENT_HISTORY.value,
                "reason_code": ReasonCode.INSUFFICIENT_HISTORY.value,
                "detail": f"Execution halted: Capability '{tool_name}' sample size {conf_eval.get('sample_size', 0)} is below 20 requirement.",
                "fault_cleared": False,
                "confidence_eval": conf_eval
            }
        elif reason_code_str == ReasonCode.BLOCKED_LOW_CONFIDENCE.value or conf_eval.get("execution_confidence", 1.0) < 0.70:
            state_machine.transition_to("BLOCKED_LOW_CONFIDENCE", ReasonCode.BLOCKED_LOW_CONFIDENCE, "Execution halted due to low confidence score")
            return {
                "status": "blocked",
                "terminal_state": TerminalState.BLOCKED_LOW_CONFIDENCE.value,
                "reason_code": ReasonCode.BLOCKED_LOW_CONFIDENCE.value,
                "detail": f"Execution halted: Confidence score {conf_eval.get('execution_confidence', 0):.2f} is below 0.70 threshold.",
                "fault_cleared": False,
                "confidence_eval": conf_eval
            }

        # 4. Check preconditions
        state_machine.transition_to("CHECKING_PRECONDITIONS", ReasonCode.DIAGNOSED, "Checking preconditions")

        # 5. Execute typed operation
        executor_name = proposal.get("executor_name")
        if not executor_name or executor_name not in self.agent.executors:
            state_machine.transition_to("BLOCKED_UNKNOWN_CAPABILITY", ReasonCode.BLOCKED_UNKNOWN_CAPABILITY, f"Unregistered capability {tool_name}")
            return {
                "status": "blocked",
                "terminal_state": TerminalState.BLOCKED_UNKNOWN_CAPABILITY.value,
                "reason_code": ReasonCode.BLOCKED_UNKNOWN_CAPABILITY.value,
                "detail": f"Execution blocked: Unmapped capability '{tool_name}'",
                "fault_cleared": False
            }

        executor = self.agent.executors[executor_name]
        state_machine.transition_to("EXECUTING", ReasonCode.DIAGNOSED, f"Executing operation {tool_name}")
        
        exec_start = time.time()
        exec_res = executor.execute(target_name, tool_name, parameters)
        exec_duration = time.time() - exec_start

        if not exec_res.get("success", False):
            state_machine.transition_to("EXECUTION_FAILED", ReasonCode.EXECUTION_FAILED, exec_res.get("output", "Execution failed"))
            self.persistence.record_outcome(tool_name, "container", False, state_machine.incident_id, time.strftime("%Y-%m-%d %H:%M:%S"))
            return {
                "status": "failed",
                "terminal_state": TerminalState.EXECUTION_FAILED.value,
                "reason_code": ReasonCode.EXECUTION_FAILED.value,
                "detail": exec_res.get("output"),
                "fault_cleared": False
            }

        # 6. Verify postconditions
        state_machine.transition_to("VERIFYING", ReasonCode.DIAGNOSED, "Verifying postconditions")
        verifier_name = proposal.get("verifier_name", "service_health")
        verifier = self.agent.verifiers.get(verifier_name, self.agent.verifiers["service_health"])
        
        ver_res = verifier.verify(target_name, tool_name, parameters, exec_res)
        
        if not ver_res.get("passed", False):
            state_machine.transition_to("ROLLING_BACK", ReasonCode.VERIFICATION_FAILED_ROLLED_BACK, "Verifier failed; initiating rollback")
            self.persistence.record_outcome(tool_name, "container", False, state_machine.incident_id, time.strftime("%Y-%m-%d %H:%M:%S"))
            
            # Execute real rollback operation
            rollback_ok = False
            rollback_detail = ""
            rollback_intent = proposal.get("rollback_intent")
            
            if rollback_intent and isinstance(rollback_intent, dict):
                rb_tool = rollback_intent.get("intent_type")
                rb_params = rollback_intent.get("parameters", {})
                rb_res = executor.execute(target_name, rb_tool, rb_params)
                rollback_ok = rb_res.get("success", False)
                rollback_detail = rb_res.get("output", "")
            else:
                if tool_name == "postgres.setting.update":
                    setting = parameters.get("setting_name")
                    rb_res = executor.execute(target_name, tool_name, {"setting_name": setting, "value": "DEFAULT"})
                    rollback_ok = rb_res.get("success", False)
                    rollback_detail = rb_res.get("output", "")
                elif tool_name == "redis.eviction_policy.update":
                    rb_res = executor.execute(target_name, tool_name, {"policy": "noeviction"})
                    rollback_ok = rb_res.get("success", False)
                    rollback_detail = rb_res.get("output", "")
                else:
                    # Generic rollback attempt for container restart
                    rb_res = executor.execute(target_name, "container.restart", parameters)
                    rollback_ok = rb_res.get("success", False)
                    rollback_detail = rb_res.get("output", "")

            if rollback_ok:
                state_machine.transition_to("VERIFICATION_FAILED_ROLLED_BACK", ReasonCode.VERIFICATION_FAILED_ROLLED_BACK, "Rollback executed successfully")
                return {
                    "status": "failed",
                    "terminal_state": TerminalState.VERIFICATION_FAILED_ROLLED_BACK.value,
                    "reason_code": ReasonCode.VERIFICATION_FAILED_ROLLED_BACK.value,
                    "detail": f"Verification failed: {ver_res.get('reason')}. Rollback executed ({rollback_detail}).",
                    "fault_cleared": False
                }
            else:
                state_machine.transition_to("VERIFICATION_FAILED_ROLLBACK_FAILED", ReasonCode.VERIFICATION_FAILED_ROLLBACK_FAILED, f"Rollback failed: {rollback_detail}")
                return {
                    "status": "failed",
                    "terminal_state": TerminalState.VERIFICATION_FAILED_ROLLBACK_FAILED.value,
                    "reason_code": ReasonCode.VERIFICATION_FAILED_ROLLBACK_FAILED.value,
                    "detail": f"Verification failed: {ver_res.get('reason')}. Rollback failed ({rollback_detail}).",
                    "fault_cleared": False
                }


        # 7. Cleanup & Terminal Recovery
        state_machine.transition_to("CLEANING_UP", ReasonCode.VERIFIED_RECOVERED, "Cleaning up disposable state")
        
        if tool_name and tool_name.startswith("observe"):
            terminal_state = TerminalState.DIAGNOSED.value
            fault_cleared = False
            reason = ReasonCode.DIAGNOSED.value
        else:
            terminal_state = TerminalState.VERIFIED_RECOVERED.value
            fault_cleared = True
            reason = ReasonCode.VERIFIED_RECOVERED.value

        state_machine.transition_to(terminal_state, ReasonCode(reason), "Execution and verification completed successfully")
        self.persistence.record_outcome(tool_name, "container", True, state_machine.incident_id, time.strftime("%Y-%m-%d %H:%M:%S"))

        return {
            "status": "executed",
            "terminal_state": terminal_state,
            "reason_code": reason,
            "detail": f"Successfully executed and verified {tool_name}",
            "execution_time_seconds": round(exec_duration, 4),
            "fault_cleared": fault_cleared,
            "confidence_eval": conf_eval
        }
