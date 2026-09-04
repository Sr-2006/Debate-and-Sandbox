"""Integration tests against local NATS JetStream server using Laptop1 Stage A stream conventions."""

import asyncio
import json
import os
import tempfile
import pytest
import nats

from transport.canonical_json import compute_payload_sha256
from transport.nats_receiver import (
    Laptop2IncidentReceiver,
    DEFAULT_STREAM,
    DEFAULT_SUBJECT,
    RECEIPT_SUBJECT,
    DEFAULT_CONSUMER
)
from transport.contracts import EventStatus


def test_live_nats_jetstream_receiver_flow():
    """Verify live NATS JetStream receive on AUTOSRE stream, deduplication, atomic staging, and receipt publication."""
    async def _test():
        nats_url = "nats://127.0.0.1:4222"

        # Test if NATS is reachable
        try:
            nc = await nats.connect(nats_url, connect_timeout=2)
        except Exception as e:
            pytest.skip(f"Local NATS server not reachable at {nats_url}: {e}")
            return

        js = nc.jetstream()

        stream_name = "AUTOSRE"
        subject_wildcard = "autosre.>"
        incident_subject = "autosre.incident.ready.v1"
        consumer_name = "laptop2-incident-consumer-v1"

        # Delete any existing overlapping streams on test NATS
        for s_name in ["AUTOSRE_INCIDENTS", "AUTOSRE_INCIDENTS_TEST", "AUTOSRE"]:
            try:
                await js.delete_stream(s_name)
            except Exception:
                pass

        await js.add_stream(name=stream_name, subjects=[subject_wildcard])

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "transport.db")
            staging_dir = os.path.join(tmpdir, "staging")

            receiver = Laptop2IncidentReceiver(
                nats_url=nats_url,
                stream_name=stream_name,
                subject=incident_subject,
                consumer_name=consumer_name,
                state_db_path=db_path,
                input_dir=staging_dir,
                publish_receipts=True,
                create_stream_if_missing=False  # Production rule: do not create stream
            )

            # Build valid Stage A incident event
            event_id = "evt_live_stage_a_001"
            incident_id = "case_live_stage_a"
            payload = {
                "system_context": {"objective": "Live NATS Stage A test"},
                "incident_event": {"incident_id": incident_id, "severity": "HIGH"},
                "infrastructure_topology": {"role": "worker"},
                "service_health_status": {"health": "degraded"},
                "telemetry_evidence": {"log_samples": []},
                "injected_chaos_context": {"active_mutations": "none"},
                "laptop1_rl_advisory": {"recommendation": "OBSERVE_FIRST"},
                "lineage_metadata": {"collector": "stage_a"}
            }
            payload_hash = compute_payload_sha256(payload)

            valid_event = {
                "schema_version": "1.0.0",
                "event_type": "autosre.incident.ready.v1",
                "event_id": event_id,
                "root_event_id": event_id,
                "parent_event_id": None,
                "correlation_id": "corr_live_stage_a_001",
                "incident_id": incident_id,
                "phase": "STAGE_A",
                "component": "incident_engine",
                "status": "READY",
                "timestamp": "2026-09-04T00:00:00Z",
                "source": {
                    "engine": "laptop1",
                    "host": "laptop1",
                    "version": "1.0.0",
                    "dataset_version": None,
                    "git_sha": None,
                    "generated_at": None
                },
                "metrics": {},
                "integrity": {
                    "payload_sha256": payload_hash,
                    "signature": None,
                    "commit_sha": None
                },
                "payload": payload
            }

            # 1. Publish valid event to JetStream
            pub_ack = await js.publish(incident_subject, json.dumps(valid_event).encode("utf-8"))
            assert pub_ack.seq > 0

            # 2. Process message using receiver
            summary = await receiver.process_single_message(timeout=5.0)
            assert summary is not None
            assert summary["status"] == "STAGED"
            assert summary["event_id"] == event_id
            assert summary["incident_id"] == incident_id

            # 3. Verify staged envelope on disk contains event metadata and canonical blocks
            staged_path = summary["staged_path"]
            assert os.path.isfile(staged_path)
            with open(staged_path, "r", encoding="utf-8") as f:
                staged_json = json.load(f)
            assert staged_json["event_id"] == event_id
            assert staged_json["correlation_id"] == "corr_live_stage_a_001"
            assert staged_json["incident_id"] == incident_id
            assert set(staged_json["payload"].keys()).issuperset({
                "system_context",
                "incident_event",
                "infrastructure_topology",
                "service_health_status",
                "telemetry_evidence",
                "injected_chaos_context"
            })
            assert staged_json["payload"]["incident_event"]["incident_id"] == incident_id

            # 4. Verify SQLite DB state
            record = receiver.dedup_store.get_event(event_id)
            assert record is not None
            assert record["status"] == EventStatus.STAGED.value
            assert record["payload_hash"] == payload_hash

            # 5. Publish duplicate event and verify suppression
            await js.publish(incident_subject, json.dumps(valid_event).encode("utf-8"))
            summary2 = await receiver.process_single_message(timeout=5.0)
            assert summary2 is not None
            assert summary2["status"] == "ALREADY_STAGED"

            # 6. Negative test: publish event with invalid engine
            invalid_event = {
                "schema_version": "1.0.0",
                "event_type": "autosre.incident.ready.v1",
                "event_id": "evt_live_invalid_eng",
                "root_event_id": "evt_live_invalid_eng",
                "parent_event_id": None,
                "correlation_id": "corr_invalid_eng",
                "incident_id": "case_invalid_eng",
                "phase": "STAGE_A",
                "component": "incident_engine",
                "status": "READY",
                "timestamp": "2026-09-04T00:00:00Z",
                "source": {
                    "engine": "unknown_engine"
                },
                "integrity": {
                    "payload_sha256": payload_hash
                },
                "payload": payload
            }
            await js.publish(incident_subject, json.dumps(invalid_event).encode("utf-8"))
            summary_inv = await receiver.process_single_message(timeout=5.0)
            assert summary_inv is not None
            assert summary_inv["status"] in ["INVALID_SOURCE_ENGINE", "REJECTED_SCHEMA"]
            assert not os.path.exists(os.path.join(staging_dir, "evt_live_invalid_eng.json"))

            await receiver.close()

        await nc.close()

    asyncio.run(_test())


def test_missing_stream_fails_clearly():
    """Verify receiver raises RuntimeError when required stream does not exist in production mode."""
    async def _test():
        nats_url = "nats://127.0.0.1:4222"
        try:
            nc = await nats.connect(nats_url, connect_timeout=2)
            await nc.close()
        except Exception as e:
            pytest.skip(f"Local NATS server not reachable: {e}")
            return

        receiver = Laptop2IncidentReceiver(
            nats_url=nats_url,
            stream_name="NON_EXISTENT_STREAM_999",
            create_stream_if_missing=False
        )

        with pytest.raises(RuntimeError) as excinfo:
            await receiver.process_single_message(timeout=1.0)
        assert "Required JetStream stream 'NON_EXISTENT_STREAM_999' not found" in str(excinfo.value)
        await receiver.close()

    asyncio.run(_test())
