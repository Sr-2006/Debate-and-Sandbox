"""NATS JetStream Incident Receiver and Controlled Input Staging for Laptop2."""

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import nats
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import ConsumerConfig, AckPolicy, DeliverPolicy

from transport.contracts import (
    CANONICAL_BLOCKS,
    EventStatus,
    TransportReasonCode,
    TransportReceipt,
    ValidationResult
)
from transport.canonical_json import compute_payload_sha256, canonical_json_str
from transport.validation import validate_incident_event
from transport.dedup_store import DedupStore
from contracts.models import get_runtime_git_commit

DEFAULT_NATS_URL = os.environ.get("AUTOSRE_NATS_URL", "nats://127.0.0.1:4222")
DEFAULT_STREAM = os.environ.get("AUTOSRE_STREAM", "AUTOSRE")
DEFAULT_SUBJECT = os.environ.get("AUTOSRE_INCIDENT_SUBJECT", "autosre.incident.ready.v1")
DEFAULT_CONSUMER = os.environ.get("AUTOSRE_CONSUMER_NAME", "laptop2-incident-consumer-v1")
DEFAULT_STATE_DB = "runtime/transport.db"
DEFAULT_INPUT_DIR = "runtime/transport_inputs"
RECEIPT_SUBJECT = "autosre.transport.receipt.v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stage_payload_atomically(
    payload: Dict[str, Any],
    event_id: str,
    input_dir: str = DEFAULT_INPUT_DIR
) -> str:
    """
    Atomically writes exactly the six canonical blocks of the payload into:
    <input_dir>/<event_id>.json
    Returns the absolute path to the staged file.
    """
    os.makedirs(input_dir, exist_ok=True)
    target_path = os.path.abspath(os.path.join(input_dir, f"{event_id}.json"))
    tmp_path = target_path + ".tmp"

    # Strict subset: exactly the 6 canonical blocks
    staged_content = {
        block: payload[block] for block in CANONICAL_BLOCKS if block in payload
    }

    content_str = json.dumps(staged_content, indent=2, ensure_ascii=False)

    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content_str)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, target_path)
    return target_path


class Laptop2IncidentReceiver:
    """Handles transport event receipt, validation, deduplication, and atomic staging."""

    def __init__(
        self,
        nats_url: str = DEFAULT_NATS_URL,
        stream_name: str = DEFAULT_STREAM,
        subject: str = DEFAULT_SUBJECT,
        consumer_name: str = DEFAULT_CONSUMER,
        state_db_path: str = DEFAULT_STATE_DB,
        input_dir: str = DEFAULT_INPUT_DIR,
        publish_receipts: bool = True,
        create_stream_if_missing: bool = False
    ):
        self.nats_url = nats_url
        self.stream_name = stream_name
        self.subject = subject
        self.consumer_name = consumer_name
        self.input_dir = input_dir
        self.publish_receipts = publish_receipts
        self.create_stream_if_missing = create_stream_if_missing
        self.dedup_store = DedupStore(state_db_path)
        self.nc: Optional[nats.NATS] = None
        self.js: Optional[nats.js.JetStreamContext] = None

    def process_event_payload(
        self,
        event: Dict[str, Any]
    ) -> Tuple[bool, str, Optional[str], Optional[ValidationResult]]:
        """
        Executes pure validation, dedup evaluation, and staging logic for an event dictionary.
        Returns: (success, status_or_action, staged_input_path, validation_result)
        """
        if not isinstance(event, dict):
            return False, "INVALID_FORMAT", None, None

        event_id = event.get("event_id")
        incident_id = event.get("incident_id") or event.get("payload", {}).get("incident_event", {}).get("incident_id", "unknown")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        correlation_id = event.get("correlation_id", "unknown")

        # Compute payload hash if payload exists
        payload_hash = compute_payload_sha256(payload) if payload else "unknown"

        # --- DEDUP RULE EVALUATION ---
        if event_id:
            existing_event = self.dedup_store.get_event(event_id)
            if existing_event and existing_event.get("status") == EventStatus.STAGED.value:
                # CASE A: Same event_id already STAGED -> ACK, do not stage again
                return True, "ALREADY_STAGED", existing_event.get("input_path"), None

        # Check semantic duplicate (different event_id, same incident_id + same payload_hash already STAGED)
        if incident_id != "unknown" and payload_hash != "unknown":
            existing_semantic = self.dedup_store.find_payload(incident_id, payload_hash)
            if existing_semantic and existing_semantic.get("status") == EventStatus.STAGED.value:
                # CASE C: Semantic duplicate -> ACK, do not create duplicate file
                if event_id and not self.dedup_store.has_event(event_id):
                    self.dedup_store.record_received(event_id, incident_id, payload_hash, correlation_id)
                    self.dedup_store.mark_staged(event_id, existing_semantic.get("input_path", ""))
                return True, "SEMANTIC_DUPLICATE_STAGED", existing_semantic.get("input_path"), None

        # Record initial receipt in state store
        if event_id:
            self.dedup_store.record_received(event_id, incident_id, payload_hash, correlation_id)

        # Strict validation
        val_res = validate_incident_event(event)
        if not val_res.is_valid:
            if event_id:
                self.dedup_store.mark_failed(event_id, f"{val_res.reason_code.value}: {'; '.join(val_res.errors)}")
            return False, val_res.reason_code.value, None, val_res

        # Mark VALIDATED
        if event_id:
            self.dedup_store.mark_validated(event_id)

        # Atomically stage the payload
        try:
            staged_path = stage_payload_atomically(payload, event_id, self.input_dir)
            self.dedup_store.mark_staged(event_id, staged_path)
            return True, "STAGED", staged_path, val_res
        except Exception as e:
            err_msg = f"STAGING_ERROR: {str(e)}"
            if event_id:
                self.dedup_store.mark_failed(event_id, err_msg)
            return False, "STAGING_FAILED", None, val_res

    async def connect(self) -> None:
        """Connects to NATS JetStream server."""
        self.nc = await nats.connect(self.nats_url)
        self.js = self.nc.jetstream()

    async def close(self) -> None:
        """Closes NATS connection cleanly."""
        if self.nc and not self.nc.is_closed:
            await self.nc.drain()
            await self.nc.close()

    async def _ensure_consumer(self) -> str:
        """Ensures the durable pull consumer exists on the existing stream."""
        if not self.js:
            raise RuntimeError("JetStream context not initialized")

        # Discover or verify stream exists
        try:
            await self.js.stream_info(self.stream_name)
        except Exception as e:
            if self.create_stream_if_missing:
                try:
                    await self.js.add_stream(
                        name=self.stream_name,
                        subjects=[self.subject, f"{self.subject}.*"]
                    )
                except Exception:
                    pass
            else:
                raise RuntimeError(
                    f"Required JetStream stream '{self.stream_name}' not found. "
                    "Laptop2 receiver requires the existing stream created by Laptop1."
                ) from e

        # Create or update durable pull consumer on existing stream
        try:
            await self.js.add_consumer(
                stream=self.stream_name,
                config=ConsumerConfig(
                    durable_name=self.consumer_name,
                    filter_subject=self.subject,
                    ack_policy=AckPolicy.EXPLICIT,
                    deliver_policy=DeliverPolicy.ALL,
                    ack_wait=30.0,
                    max_deliver=5
                )
            )
        except Exception:
            pass

        return self.stream_name

    async def publish_receipt(
        self,
        event: Dict[str, Any],
        staged_path: str
    ) -> None:
        """Publishes autosre.transport.receipt.v1 event if NATS JetStream is active."""
        if not self.publish_receipts or not self.js:
            return

        receipt = TransportReceipt(
            schema_version="1.0",
            event_type="autosre.transport.receipt",
            event_id=f"evt_receipt_{uuid.uuid4().hex[:12]}",
            parent_event_id=event.get("event_id", ""),
            correlation_id=event.get("correlation_id", ""),
            incident_id=event.get("incident_id", ""),
            status="STAGED",
            received_at=_now_iso(),
            laptop2_commit=get_runtime_git_commit()
        )

        try:
            payload_bytes = json.dumps(receipt.to_dict()).encode("utf-8")
            await self.js.publish(RECEIPT_SUBJECT, payload_bytes)
        except Exception:
            # Receipt publication should not fail local durable receipt
            pass

    async def process_single_message(
        self,
        timeout: float = 10.0
    ) -> Optional[Dict[str, Any]]:
        """
        Pulls exactly one message from JetStream, processes it through the pipeline,
        persists state, stages file, and sends explicit ACK.
        """
        if not self.js:
            await self.connect()

        stream_name = await self._ensure_consumer()
        sub = await self.js.pull_subscribe(
            subject=self.subject,
            durable=self.consumer_name,
            stream=stream_name
        )

        try:
            msgs = await sub.fetch(batch=1, timeout=timeout)
        except (NatsTimeoutError, asyncio.TimeoutError):
            return None

        if not msgs:
            return None

        msg = msgs[0]
        summary: Dict[str, Any] = {
            "subject": msg.subject,
            "seq": msg.metadata.sequence.stream if msg.metadata else None,
            "processed_at": _now_iso()
        }

        try:
            data_str = msg.data.decode("utf-8")
            event = json.loads(data_str)
        except Exception as e:
            # Poison non-JSON message -> terminate/ack so it doesn't loop infinitely
            try:
                await msg.term()
            except Exception:
                await msg.ack()
            summary["status"] = "MALFORMED_JSON"
            summary["error"] = str(e)
            return summary

        success, status, staged_path, val_res = self.process_event_payload(event)
        summary["event_id"] = event.get("event_id")
        summary["incident_id"] = event.get("incident_id")
        summary["correlation_id"] = event.get("correlation_id")
        summary["status"] = status
        summary["staged_path"] = staged_path

        if success:
            # Durable state and file are safely on disk -> ACK
            await msg.ack()
            if status == "STAGED" and staged_path:
                await self.publish_receipt(event, staged_path)
        else:
            # If invalid schema / payload hash, term message to avoid poison loops
            try:
                await msg.term()
            except Exception:
                await msg.ack()
            summary["errors"] = val_res.errors if val_res else [status]

        return summary
