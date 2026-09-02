from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from contracts.reason_codes import ReasonCode, TerminalState

VALID_TRANSITIONS = {
    "RECEIVED": ["VALIDATING", "REJECTED_SCHEMA", "DUPLICATE_IGNORED"],
    "VALIDATING": ["CLAIMED", "REJECTED_SCHEMA", "INVALID_ACTION_FORMAT", "PLACEHOLDER_DETECTED"],
    "CLAIMED": ["ATTESTING", "DUPLICATE_IGNORED"],
    "ATTESTING": ["RESOLVING_CAPABILITY", "ATTESTATION_FAILED"],
    "RESOLVING_CAPABILITY": ["CHECKING_POLICY", "BLOCKED_UNKNOWN_CAPABILITY", "BLOCKED_TARGET_UNRESOLVED"],
    "CHECKING_POLICY": ["CHECKING_CONFIDENCE", "BLOCKED_POLICY", "BLOCKED_SAFETY", "REQUIRES_HUMAN_APPROVAL"],
    "CHECKING_CONFIDENCE": ["SETTING_UP_FAULT", "DIAGNOSED", "BLOCKED_LOW_CONFIDENCE", "INSUFFICIENT_HISTORY"],
    "SETTING_UP_FAULT": ["CHECKING_PRECONDITIONS", "FAULT_SETUP_FAILED"],
    "CHECKING_PRECONDITIONS": ["EXECUTING", "PRECONDITION_FAILED"],
    "EXECUTING": ["VERIFYING", "ROLLING_BACK", "EXECUTION_FAILED"],
    "VERIFYING": ["CLEANING_UP", "ROLLING_BACK", "VERIFIED_RECOVERED"],
    "ROLLING_BACK": ["VERIFICATION_FAILED_ROLLED_BACK", "VERIFICATION_FAILED_ROLLBACK_FAILED"],
    "CLEANING_UP": ["VERIFIED_RECOVERED", "DIAGNOSED", "MUTATION_APPLIED_UNVERIFIED"]
}

class ExecutionStateMachine:
    """State Machine tracking remediation transitions across the 7 MVP pipeline states."""

    def __init__(self, incident_id: str, payload_hash: str):
        self.incident_id = incident_id
        self.payload_hash = payload_hash
        self.current_state = "RECEIVED"
        self.history: List[Dict[str, Any]] = []
        self.reason_code = ReasonCode.DIAGNOSED
        self._record_transition("RECEIVED", ReasonCode.DIAGNOSED, "Payload received")

    def transition_to(self, new_state: str, reason_code: ReasonCode, message: str = "") -> bool:
        """Transitions state machine to new_state and records history item."""
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

