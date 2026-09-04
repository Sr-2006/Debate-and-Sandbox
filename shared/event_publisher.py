"""Async NATS JetStream Event Publisher for AutoSRE."""

import json
import logging
from typing import Any, Dict, Optional

import nats
from nats.js.api import PubAck

from shared.event_envelope import validate_event_envelope

logger = logging.getLogger(__name__)


class EventPublisher:
    """Publishes validated event envelopes to NATS JetStream subjects."""

    def __init__(
        self,
        nats_url: str = "nats://127.0.0.1:4222",
        stream_name: str = "AUTOSRE",
        connect_timeout: float = 5.0
    ):
        self.nats_url = nats_url
        self.stream_name = stream_name
        self.connect_timeout = connect_timeout
        self.nc: Optional[nats.NATS] = None
        self.js = None

    async def connect(self):
        """Establishes connection to NATS broker and initializes JetStream context."""
        if self.nc and not self.nc.is_closed:
            return
        self.nc = await nats.connect(self.nats_url, connect_timeout=self.connect_timeout)
        self.js = self.nc.jetstream()

    async def publish_event(
        self,
        subject: str,
        envelope: Dict[str, Any],
        timeout: float = 5.0
    ) -> Dict[str, Any]:
        """Validates envelope and publishes it to the specified subject with JetStream PubAck."""
        is_valid, err = validate_event_envelope(envelope)
        if not is_valid:
            raise ValueError(f"Invalid event envelope: {err}")

        await self.connect()

        payload_bytes = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        ack: PubAck = await self.js.publish(subject, payload_bytes, timeout=timeout)

        return {
            "status": "PUBLISHED",
            "stream": ack.stream,
            "seq": ack.seq,
            "subject": subject,
            "event_id": envelope.get("event_id")
        }

    async def close(self):
        """Closes NATS connection."""
        if self.nc and not self.nc.is_closed:
            try:
                await self.nc.drain()
            except Exception:
                pass
            try:
                await self.nc.close()
            except Exception:
                pass
            self.nc = None
            self.js = None
