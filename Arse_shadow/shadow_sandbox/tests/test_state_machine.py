import pytest
from shadow_sandbox.state_machine import ExecutionStateMachine
from contracts.reason_codes import ReasonCode, TerminalState

def test_state_machine_happy_path():
    sm = ExecutionStateMachine("case_04", "hash123")
    assert sm.current_state == "RECEIVED"
    
    assert sm.transition_to("VALIDATING", ReasonCode.DIAGNOSED)
    assert sm.transition_to("CLAIMED", ReasonCode.DIAGNOSED)
    assert sm.transition_to("ATTESTING", ReasonCode.DIAGNOSED)
    assert sm.transition_to("RESOLVING_CAPABILITY", ReasonCode.DIAGNOSED)
    assert sm.transition_to("CHECKING_POLICY", ReasonCode.DIAGNOSED)
    assert sm.transition_to("CHECKING_CONFIDENCE", ReasonCode.DIAGNOSED)
    assert sm.transition_to("SETTING_UP_FAULT", ReasonCode.DIAGNOSED)
    assert sm.transition_to("CHECKING_PRECONDITIONS", ReasonCode.DIAGNOSED)
    assert sm.transition_to("EXECUTING", ReasonCode.DIAGNOSED)
    assert sm.transition_to("VERIFYING", ReasonCode.DIAGNOSED)
    assert sm.transition_to("CLEANING_UP", ReasonCode.VERIFIED_RECOVERED)
    assert sm.transition_to(TerminalState.VERIFIED_RECOVERED.value, ReasonCode.VERIFIED_RECOVERED)

    summary = sm.get_summary()
    assert summary["terminal_state"] == "VERIFIED_RECOVERED"
    assert summary["transition_count"] == 13
