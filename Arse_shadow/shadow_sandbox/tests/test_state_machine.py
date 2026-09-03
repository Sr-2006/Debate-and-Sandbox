import pytest
from shadow_sandbox.state_machine import ExecutionStateMachine
from contracts.reason_codes import ReasonCode, TerminalState

def test_state_machine_happy_path():
    sm = ExecutionStateMachine("case_04", "hash123")
    assert sm.current_state == "RECEIVED"
    
    assert sm.transition_to("VALIDATED", ReasonCode.DIAGNOSED)
    assert sm.transition_to("OBSERVED_BEFORE", ReasonCode.DIAGNOSED)
    assert sm.transition_to("EXECUTED_OR_BLOCKED", ReasonCode.DIAGNOSED)
    assert sm.transition_to("OBSERVED_AFTER", ReasonCode.DIAGNOSED)
    assert sm.transition_to("VERIFIED_OR_ROLLED_BACK", ReasonCode.VERIFIED_RECOVERED)
    assert sm.transition_to("REPORTED", ReasonCode.VERIFIED_RECOVERED)
    assert sm.current_state == "REPORTED"

def test_state_machine_invalid_transition_rejected():
    sm = ExecutionStateMachine("case_invalid", "hash456")
    assert sm.current_state == "RECEIVED"
    # Attempt illegal transition straight from RECEIVED to OBSERVED_AFTER
    ok = sm.transition_to("OBSERVED_AFTER", ReasonCode.DIAGNOSED)
    assert ok is False
    assert sm.current_state == "RECEIVED"


