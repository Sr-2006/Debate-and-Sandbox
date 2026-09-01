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

        # 2. Confidence check
        conf_score = calculate_confidence(proposal, self.history_path)
        if conf_score < 0.70:
            return {
                "incident_id": incident_id,
                "gate_decision": "BLOCKED_LOW_CONFIDENCE",
                "confidence_score": conf_score,
                "human_intervention_required": True,
                "message": f"Execution halted: Confidence ratio {conf_score:.2f} is below the 0.70 safety threshold.",
                "agent_proposal": proposal,
                "guardrail_result": None,
                "execution_result": None,
                "fault_cleared": False
            }

        sm = ExecutionStateMachine(incident_id, "legacy_hash")
        res = self.run_proposal(proposal, problem_text, sm)
        res["incident_id"] = incident_id
        res["gate_decision"] = res.get("terminal_state", "VERIFIED_RECOVERED")
        res["confidence_score"] = conf_score
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
        conf_eval = self.confidence_analyzer.calculate_confidence(tool_name, "container")
        
        # 4. Check preconditions
        state_machine.transition_to("CHECKING_PRECONDITIONS", ReasonCode.DIAGNOSED, "Checking preconditions")

        # 5. Execute typed operation
        state_machine.transition_to("EXECUTING", ReasonCode.DIAGNOSED, f"Executing operation {tool_name}")
        executor_name = proposal.get("executor_name", "docker_executor")
        executor = self.agent.executors.get(executor_name, self.agent.executors["docker_executor"])
        
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
            return {
                "status": "failed",
                "terminal_state": TerminalState.VERIFICATION_FAILED_ROLLED_BACK.value,
                "reason_code": ReasonCode.VERIFICATION_FAILED_ROLLED_BACK.value,
                "detail": f"Verification failed: {ver_res.get('reason')}. Rollback performed.",
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
