"""Comprehensive tests for Laptop 2 Event Lifecycle Pipeline & RL Unification."""

import asyncio
import json
import os
import tempfile
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shared.subjects import (
    SUBJECT_INCIDENT_READY,
    SUBJECT_TRANSPORT_RECEIVED,
    SUBJECT_PHASE3_STARTED,
    SUBJECT_PHASE3_DEBATE,
    SUBJECT_PHASE3_COMPLETED,
    SUBJECT_RL_LAPTOP2_ADVISORY,
    SUBJECT_PHASE4_STARTED,
    SUBJECT_PHASE4_ATTESTATION,
    SUBJECT_PHASE4_EXECUTION,
    SUBJECT_PHASE4_VERIFICATION,
    SUBJECT_PHASE4_COMPLETED,
    SUBJECT_RL_FEEDBACK,
    SUBJECT_PIPELINE_COMPLETED,
    SUBJECT_PIPELINE_FAILED,
    SUBJECT_TELEMETRY_LOGS,
    SUBJECT_TELEMETRY_METRICS,
)
from shared.event_envelope import (
    build_event_envelope,
    validate_event_envelope,
    compute_sha256,
    canonical_json_str,
    canonical_json_bytes,
)
from shared.action_registry import (
    ACTION_DATABASE_TUNE,
    ACTION_RESTART_POD,
    ACTION_SCALE_UP_REPLICAS,
    ACTION_CONFIG_UPDATE,
    ACTION_CIRCUIT_BREAK,
    ACTION_FLUSH_CACHE,
    ACTION_FLUSH_THREAD_POOL,
    ACTION_FAILOVER_DB_REPLICA,
    ACTION_NO_ACTION,
    UNIFIED_POLICY_ACTIONS,
    get_capability_for_policy_action,
    get_policy_action_for_intent,
    is_valid_policy_action,
)
from shared.telemetry import emit_log_event, emit_metric_event
from rl_engine.config import RL_FEATURE_VERSION, RL_POLICY_VERSION, RL_REWARD_VERSION, RL_ROLE
from rl_engine.advisor import RLAdvisor
from rl_engine.feedback import build_rl_feedback_payload, calculate_remediation_reward
from transport.processing_worker import Laptop2ProcessingWorker
from transport.dedup_store import DedupStore


class TestEventEnvelopeContracts:
    """Validates the standardized 15-field EventEnvelope schema and integrity hashing."""

    def test_envelope_15_field_structure(self):
        root_id = "evt_root_123"
        parent_id = "evt_parent_456"
        corr_id = "corr_789"
        inc_id = "inc_order_51"
        payload = {"problem": "high_cpu", "details": {"cpu": 98.5}}

        env = build_event_envelope(
            event_type="autosre.phase3.started",
            root_event_id=root_id,
            parent_event_id=parent_id,
            correlation_id=corr_id,
            incident_id=inc_id,
            phase="PHASE3",
            component="orchestrator",
            status="STARTED",
            payload=payload,
            metrics={"cpu_pct": 98.5},
            source_engine="laptop2"
        )

        # Verify all 15 fields exist
        expected_fields = [
            "schema_version", "event_id", "parent_event_id", "root_event_id",
            "correlation_id", "incident_id", "phase", "component",
            "event_type", "status", "timestamp", "source",
            "payload", "metrics", "integrity"
        ]
        for field in expected_fields:
            assert field in env, f"Field '{field}' missing from envelope"

        assert env["root_event_id"] == root_id
        assert env["parent_event_id"] == parent_id
        assert env["correlation_id"] == corr_id
        assert env["incident_id"] == inc_id
        assert env["phase"] == "PHASE3"
        assert env["component"] == "orchestrator"
        assert env["status"] == "STARTED"
        assert env["source"]["engine"] == "laptop2"

        # Validate with validator
        is_valid, err = validate_event_envelope(env)
        assert is_valid is True, f"Validation failed: {err}"
        assert err is None

    def test_envelope_integrity_hash_verification(self):
        payload = {"action": "restart", "target": "payment-pod-1"}
        env = build_event_envelope(
            event_type="autosre.phase4.execution",
            root_event_id="evt_root_1",
            parent_event_id="evt_parent_1",
            correlation_id="corr_1",
            incident_id="inc_1",
            phase="PHASE4",
            component="executor",
            status="EXECUTED",
            payload=payload
        )

        assert env["integrity"]["payload_sha256"] == compute_sha256(payload)

        # Tampering with payload should fail validation
        env["payload"]["tampered"] = True
        is_valid, err = validate_event_envelope(env)
        assert is_valid is False
        assert "Payload SHA256 mismatch" in err


class TestActionRegistryAndRLUnification:
    """Validates unified action registry and RL integration."""

    def test_unified_policy_actions_completeness(self):
        expected_actions = [
            "DATABASE_TUNE", "RESTART_POD", "SCALE_UP_REPLICAS", "CONFIG_UPDATE",
            "CIRCUIT_BREAK", "FLUSH_CACHE", "FLUSH_THREAD_POOL", "FAILOVER_DB_REPLICA",
            "NO_ACTION"
        ]
        for act in expected_actions:
            assert act in UNIFIED_POLICY_ACTIONS
            assert is_valid_policy_action(act)

    def test_policy_action_to_capability_mapping(self):
        assert get_capability_for_policy_action(ACTION_DATABASE_TUNE) == "postgres.setting.update"
        assert get_capability_for_policy_action(ACTION_RESTART_POD) == "k8s.pod.restart"
        assert get_capability_for_policy_action(ACTION_SCALE_UP_REPLICAS) == "k8s.deployment.scale"
        assert get_capability_for_policy_action(ACTION_NO_ACTION) is None

    def test_capability_to_policy_action_mapping(self):
        assert get_policy_action_for_intent("postgres.setting.update") == ACTION_DATABASE_TUNE
        assert get_policy_action_for_intent("k8s.pod.restart") == ACTION_RESTART_POD
        assert get_policy_action_for_intent("container.restart") == ACTION_RESTART_POD
        assert get_policy_action_for_intent("unknown_capability") == ACTION_NO_ACTION

    def test_rl_advisor_produces_unified_vocabulary(self):
        advisor = RLAdvisor(model_version="cold-start")
        envelope = {
            "incident_id": "case_test_1",
            "intents": [{
                "intent_type": "postgres.setting.update",
                "mode": "MUTATE_REVERSIBLE",
                "risk_class": "LOW",
                "target_ref": {"canonical_name": "orders-db", "kind": "database"}
            }],
            "phase3_confidence": {"score": 0.85}
        }
        adv = advisor.generate_advisory(envelope)
        assert adv.role == RL_ROLE
        assert adv.policy_action == ACTION_DATABASE_TUNE
        assert adv.execution_capability == "postgres.setting.update"
        assert adv.advisory_decision in ["ACCEPT_PROPOSAL", "OBSERVE_FIRST", "REQUIRE_HUMAN_REVIEW", "ABSTAIN"]

    def test_rl_feedback_payload_and_rewards(self):
        advisory = {
            "policy_action": "DATABASE_TUNE",
            "execution_capability": "postgres.setting.update",
            "feature_hash": "abc123hash",
            "policy": {"policy_version": RL_POLICY_VERSION}
        }
        phase4_result = {
            "execution": {"capability": "postgres.setting.update", "status": "EXECUTED"},
            "verification": {"status": "PASSED"},
            "rollback": {"status": "NOT_RUN"}
        }
        payload = build_rl_feedback_payload(advisory, phase4_result, "SANDBOX_VERIFIED")

        assert payload["recommended_action"] == "DATABASE_TUNE"
        assert payload["executed_action"] == "postgres.setting.update"
        assert payload["outcome"] == "SANDBOX_VERIFIED"
        assert payload["reward"] == 1.0
        assert payload["reward_schema_version"] == RL_REWARD_VERSION


class TestTelemetryParallelism:
    """Verifies that telemetry logs and metrics are emitted in parallel and do not corrupt lineage."""

    def test_telemetry_emission_does_not_advance_parent(self):
        async def _run():
            mock_publisher = AsyncMock()
            mock_publisher.publish_event = AsyncMock(return_value={"status": "PUBLISHED"})

            root_id = "evt_root_001"
            curr_parent = "evt_parent_001"
            corr_id = "corr_001"
            inc_id = "inc_001"

            # Emit log
            await emit_log_event(
                publisher=mock_publisher,
                root_event_id=root_id,
                correlation_id=corr_id,
                incident_id=inc_id,
                phase="PHASE3",
                component="orchestrator",
                level="INFO",
                message="Test log message",
                parent_event_id=curr_parent
            )

            assert mock_publisher.publish_event.call_count == 1
            call_subject, call_envelope = mock_publisher.publish_event.call_args_list[0][0]
            assert call_subject == SUBJECT_TELEMETRY_LOGS
            assert call_envelope["root_event_id"] == root_id
            assert call_envelope["parent_event_id"] == curr_parent
            assert call_envelope["event_type"] == "autosre.telemetry.logs"

            # Emit metric
            await emit_metric_event(
                publisher=mock_publisher,
                root_event_id=root_id,
                correlation_id=corr_id,
                incident_id=inc_id,
                phase="PHASE4",
                component="verifier",
                metric_name="verification_latency_ms",
                value=124.5,
                parent_event_id=curr_parent
            )

            assert mock_publisher.publish_event.call_count == 2
            call_subject_2, call_envelope_2 = mock_publisher.publish_event.call_args_list[1][0]
            assert call_subject_2 == SUBJECT_TELEMETRY_METRICS
            assert call_envelope_2["metrics"]["verification_latency_ms"] == 124.5

        asyncio.run(_run())
class TestLaptop2ProcessingWorkerEventPipeline:
    """Verifies the complete 13-stage lifecycle event chain and lineage invariant in the worker."""

    def test_complete_13_stage_lifecycle_chain(self):
        async def _run():
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = os.path.join(temp_dir, "test_transport.db")
                input_dir = os.path.join(temp_dir, "transport_inputs")
                os.makedirs(input_dir, exist_ok=True)

                dedup = DedupStore(db_path)
                root_id = "evt_root_l1_001"
                ready_event_id = "evt_ready_l1_002"
                corr_id = "corr_l1_003"
                inc_id = "order-service_51"

                # Create sample staged incident file
                payload_data = {
                    "incident_event": {
                        "incident_id": inc_id,
                        "anomaly_type": "high_memory",
                        "target_service": "order-service"
                    },
                    "service_context": {},
                    "topology_slice": {},
                    "evidence_manifest": {},
                    "hypotheses_log": {},
                    "telemetry_correlations": {}
                }
                payload_sha = compute_sha256(payload_data)

                staged_envelope = {
                    "schema_version": "1.0.0",
                    "event_id": ready_event_id,
                    "parent_event_id": "evt_prev_001",
                    "root_event_id": root_id,
                    "correlation_id": corr_id,
                    "incident_id": inc_id,
                    "payload": payload_data
                }
                staged_file_path = os.path.join(input_dir, f"{ready_event_id}.json")
                with open(staged_file_path, "w", encoding="utf-8") as sf:
                    json.dump(staged_envelope, sf)

                # Stage in dedup store
                dedup.record_received(ready_event_id, inc_id, payload_sha, corr_id)
                dedup.mark_validated(ready_event_id)
                dedup.mark_staged(ready_event_id, staged_file_path)

                # Prepare worker with intercepted published events
                published_events = []

                worker = Laptop2ProcessingWorker(
                    state_db_path=db_path,
                    nats_url="nats://127.0.0.1:4222",
                    stream_name="AUTOSRE"
                )

                # Create sample report data
                mock_report_path = os.path.join(temp_dir, "phase34_report.json")
                mock_report_data = {
                    "problem": {"case_id": inc_id},
                    "run": {"verification_run_id": "run_test_123"},
                    "phase3": {
                        "rounds": [{"round": 1}],
                        "transcript_summary": "Debate reached consensus on database tuning.",
                        "winning_proposal": {"proposer": "AgentA", "action": "DATABASE_TUNE"},
                        "confidence": {"score": 0.92}
                    },
                    "rl_advisory": {
                        "advisory_id": "adv_test_01",
                        "role": "POST_DEBATE_PRE_EXECUTION",
                        "policy_action": "DATABASE_TUNE",
                        "execution_capability": "postgres.setting.update",
                        "recommendation": "ACCEPT_PROPOSAL",
                        "advisory_decision": "ACCEPT_PROPOSAL",
                        "advisory_confidence": 0.90,
                        "uncertainty": 0.1,
                        "cold_start": False,
                        "influence_allowed": True,
                        "feature_hash": "feathash_001"
                    },
                    "phase4": {
                        "attestation": {"status": "PASSED"},
                        "execution": {"capability": "postgres.setting.update", "status": "EXECUTED", "target": "orders-db"},
                        "verification": {"status": "PASSED"},
                        "rollback": {"status": "NOT_RUN"}
                    },
                    "final_summary": {
                        "outcome": "SANDBOX_VERIFIED",
                        "total_duration_ms": 350.0
                    }
                }
                with open(mock_report_path, "w", encoding="utf-8") as rf:
                    json.dump(mock_report_data, rf)

                summary_result = {
                    "json_report": mock_report_path,
                    "outcome": "SANDBOX_VERIFIED",
                    "incident_id": inc_id,
                    "verification_run_id": "run_test_123"
                }

                # Intercept publisher
                async def intercept_publish(subject, env, timeout=5.0):
                    published_events.append((subject, env))
                    return {"status": "PUBLISHED", "stream": "AUTOSRE", "seq": len(published_events), "subject": subject, "event_id": env["event_id"]}

                with patch.object(Laptop2ProcessingWorker, "_run_pipeline_subprocess", return_value=(0, "[PIPELINE_RESULT_JSON]" + json.dumps(summary_result) + "[/PIPELINE_RESULT_JSON]", "", summary_result)), \
                     patch("transport.processing_worker.EventPublisher.connect", new_callable=AsyncMock), \
                     patch("transport.processing_worker.EventPublisher.publish_event", side_effect=intercept_publish), \
                     patch("transport.processing_worker.EventPublisher.close", new_callable=AsyncMock), \
                     patch.object(Laptop2ProcessingWorker, "_publish_legacy_result", new_callable=AsyncMock, return_value={"event": {"event_id": "evt_legacy_01"}, "publish_result": {"status": "PUBLISHED"}}):

                    res = await worker.process_event_async(parent_event_id=ready_event_id)
                    assert res["status"] == "PROCESSING_COMPLETE"
                    assert res["final_outcome"] == "SANDBOX_VERIFIED"
                    assert res["root_event_id"] == root_id

                # Verify the published subjects sequence
                lifecycle_subjects = [
                    subj for subj, env in published_events
                    if subj not in [SUBJECT_TELEMETRY_LOGS, SUBJECT_TELEMETRY_METRICS]
                ]
                expected_subject_chain = [
                    SUBJECT_TRANSPORT_RECEIVED,
                    SUBJECT_PHASE3_STARTED,
                    SUBJECT_PHASE3_DEBATE,
                    SUBJECT_PHASE3_COMPLETED,
                    SUBJECT_RL_LAPTOP2_ADVISORY,
                    SUBJECT_PHASE4_STARTED,
                    SUBJECT_PHASE4_ATTESTATION,
                    SUBJECT_PHASE4_EXECUTION,
                    SUBJECT_PHASE4_VERIFICATION,
                    SUBJECT_PHASE4_COMPLETED,
                    SUBJECT_RL_FEEDBACK,
                    SUBJECT_PIPELINE_COMPLETED,
                ]
                assert lifecycle_subjects == expected_subject_chain

                # Verify strict lineage chain and 15-field validation
                prev_event_id = ready_event_id
                for subj, env in published_events:
                    # 15-field validation
                    is_val, err = validate_event_envelope(env)
                    assert is_val is True, f"Envelope validation failed for {subj}: {err}"

                    # Invariant lineage check
                    assert env["root_event_id"] == root_id, f"root_event_id violated in {subj}"
                    assert env["correlation_id"] == corr_id, f"correlation_id violated in {subj}"
                    assert env["incident_id"] == inc_id, f"incident_id violated in {subj}"

                    # Strict sequential parent pointer check for lifecycle events
                    if subj not in [SUBJECT_TELEMETRY_LOGS, SUBJECT_TELEMETRY_METRICS]:
                        assert env["parent_event_id"] == prev_event_id, (
                            f"Lineage broken at subject {subj}: expected parent {prev_event_id}, got {env['parent_event_id']}"
                        )
                        prev_event_id = env["event_id"]

        asyncio.run(_run())

    def test_pipeline_failure_cleanly_terminates_with_failed_event(self):
        async def _run():
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = os.path.join(temp_dir, "test_transport_fail.db")
                input_dir = os.path.join(temp_dir, "transport_inputs")
                os.makedirs(input_dir, exist_ok=True)

                dedup = DedupStore(db_path)
                root_id = "evt_root_fail_001"
                ready_event_id = "evt_ready_fail_002"
                corr_id = "corr_fail_003"
                inc_id = "payment-service_99"

                payload_data = {"incident_event": {"incident_id": inc_id}}
                payload_sha = compute_sha256(payload_data)

                staged_envelope = {
                    "schema_version": "1.0.0",
                    "event_id": ready_event_id,
                    "parent_event_id": "evt_prev",
                    "root_event_id": root_id,
                    "correlation_id": corr_id,
                    "incident_id": inc_id,
                    "payload": payload_data
                }
                staged_file_path = os.path.join(input_dir, f"{ready_event_id}.json")
                with open(staged_file_path, "w", encoding="utf-8") as sf:
                    json.dump(staged_envelope, sf)

                dedup.record_received(ready_event_id, inc_id, payload_sha, corr_id)
                dedup.mark_validated(ready_event_id)
                dedup.mark_staged(ready_event_id, staged_file_path)

                published_events = []
                worker = Laptop2ProcessingWorker(state_db_path=db_path)

                async def intercept_publish(subject, env, timeout=5.0):
                    published_events.append((subject, env))
                    return {"status": "PUBLISHED", "stream": "AUTOSRE", "seq": len(published_events), "subject": subject, "event_id": env["event_id"]}

                # Simulate subprocess non-zero exit error
                with patch.object(Laptop2ProcessingWorker, "_run_pipeline_subprocess", return_value=(1, "", "Syntax error in debate manager", None)), \
                     patch("transport.processing_worker.EventPublisher.connect", new_callable=AsyncMock), \
                     patch("transport.processing_worker.EventPublisher.publish_event", side_effect=intercept_publish), \
                     patch("transport.processing_worker.EventPublisher.close", new_callable=AsyncMock):

                    res = await worker.process_event_async(parent_event_id=ready_event_id)
                    assert res["status"] == "FAILED"
                    assert res["error_code"] == "PIPELINE_EXIT_NONZERO"

                lifecycle_subjects = [
                    subj for subj, env in published_events
                    if subj not in [SUBJECT_TELEMETRY_LOGS, SUBJECT_TELEMETRY_METRICS]
                ]
                assert lifecycle_subjects == [
                    SUBJECT_TRANSPORT_RECEIVED,
                    SUBJECT_PHASE3_STARTED,
                    SUBJECT_PIPELINE_FAILED
                ]

                # Verify failed event pointed to phase3.started event id
                p3_started_env = [env for subj, env in published_events if subj == SUBJECT_PHASE3_STARTED][0]
                failed_env = published_events[-1][1]

                assert failed_env["parent_event_id"] == p3_started_env["event_id"]
                assert failed_env["root_event_id"] == root_id
                assert failed_env["correlation_id"] == corr_id
                assert failed_env["status"] == "FAILED"

        asyncio.run(_run())
