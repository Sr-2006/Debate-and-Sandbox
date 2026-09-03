import pytest
from rl_engine.advisor import RLAdvisor
from rl_engine.episode_builder import build_learning_episode
from shadow_sandbox.run_pipeline import run_phase4_pipeline


def test_integration_shadow_advisory_attached_to_pipeline():
    envelope = {
        "schema_version": "2.0",
        "event_id": "evt_integ_01",
        "event_type": "autosre.action.proposed",
        "incident_id": "case_integ_01",
        "correlation_id": "corr_integ_01",
        "fingerprint": "fp_integ_01",
        "created_at": "2026-09-03T10:00:00Z",
        "source": {"phase": "PHASE_3", "code_commit": "2a3867c"},
        "problem_summary": "Restart container",
        "target_ref": {"kind": "container", "canonical_name": "user-service"},
        "phase3_confidence": {"score": 0.85},
        "execution_tier": "tier_1",
        "safety_violation": False,
        "evidence_refs": ["log_01"],
        "intents": [
            {
                "intent_id": "int_01",
                "intent_type": "container.restart",
                "mode": "MUTATE_REVERSIBLE",
                "target_ref": {"kind": "container", "canonical_name": "user-service"},
                "parameters": {},
                "evidence_refs": ["log_01"],
                "preconditions": [],
                "postconditions": [],
                "timeout_seconds": 30,
                "max_attempts": 1,
                "risk_class": "LOW",
                "requires_human_approval": False
            }
        ],
        "human_summary": "Restart container"
    }

    advisor = RLAdvisor()
    advisory = advisor.generate_advisory(envelope, run_id="run_integ_1")
    envelope["rl_advisory"] = advisory.to_dict()

    p4_context = run_phase4_pipeline(envelope, is_simulated=True)
    assert p4_context["status"] == "SIMULATION_VERIFIED"

    episode = build_learning_episode(advisory, envelope, p4_context, "run_integ_1")
    assert episode.phase4.status == "SIMULATION_VERIFIED"
    assert episode.learning.eligible is False
