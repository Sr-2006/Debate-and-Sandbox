from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from contracts.reason_codes import ReasonCode, TerminalState

VALID_TRANSITIONS = {
    "RECEIVED": ["VALIDATED", "VALIDATION_FAILED", "BLOCKED_UNKNOWN_CAPABILITY", "NO_SUPPORTED_ACTION", "REPORTED"],
    "VALIDATED": ["OBSERVED_BEFORE", "NO_SUPPORTED_ACTION", "BLOCKED_UNKNOWN_CAPABILITY", "READ_ONLY_OBSERVED", "BLOCKED_SAFETY_VIOLATION", "UNSUPPORTED_IN_MVP", "ATTESTATION_FAILED", "REPORTED"],
    "OBSERVED_BEFORE": ["EXECUTED_OR_BLOCKED", "PRECONDITION_FAILED", "REPORTED"],
    "EXECUTED_OR_BLOCKED": ["OBSERVED_AFTER", "REPORTED"],
    "OBSERVED_AFTER": ["VERIFIED_OR_ROLLED_BACK", "REPORTED"],
    "VERIFIED_OR_ROLLED_BACK": ["REPORTED"],
    "VALIDATION_FAILED": ["REPORTED"],
    "BLOCKED_UNKNOWN_CAPABILITY": ["REPORTED"],
    "NO_SUPPORTED_ACTION": ["REPORTED"],
    "READ_ONLY_OBSERVED": ["REPORTED"],
    "BLOCKED_SAFETY_VIOLATION": ["REPORTED"],
    "UNSUPPORTED_IN_MVP": ["REPORTED"],
    "ATTESTATION_FAILED": ["REPORTED"],
    "PRECONDITION_FAILED": ["REPORTED"],
    "REPORTED": []
}


class ExecutionStateMachine:
    """State Machine tracking remediation transitions across the 7 MVP pipeline states with a strict transition allowlist."""

    def __init__(self, incident_id: str, payload_hash: str):
        self.incident_id = incident_id
        self.payload_hash = payload_hash
        self.current_state = "RECEIVED"
        self.history: List[Dict[str, Any]] = []
        self.reason_code = ReasonCode.DIAGNOSED
        self._record_transition("RECEIVED", ReasonCode.DIAGNOSED, "Payload received")

    def transition_to(self, new_state: str, reason_code: ReasonCode, message: str = "") -> bool:
        """Transitions state machine to new_state if transition is allowed in the 7-state MVP allowlist."""
        if self.current_state == "REPORTED":
            return False

        allowed = VALID_TRANSITIONS.get(self.current_state, [])
        if new_state not in allowed and new_state != "REPORTED" and new_state != self.current_state:
            return False

        self.current_state = new_state
        self.reason_code = reason_code
        self._record_transition(new_state, reason_code, message)
        return True

    def _record_transition(self, state: str, reason_code: ReasonCode, message: str):
        self.history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": state,
            "reason_code": reason_code.value if hasattr(reason_code, "value") else str(reason_code),
            "message": message
        })

    def get_summary(self) -> Dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "payload_hash": self.payload_hash,
            "terminal_state": self.current_state,
            "reason_code": self.reason_code.value if hasattr(self.reason_code, "value") else str(self.reason_code),
            "transition_count": len(self.history),
            "history": self.history
        }
