"""Telemetry logging and metrics emission over NATS JetStream."""

import time
from typing import Any, Dict, Optional

from shared.subjects import SUBJECT_TELEMETRY_LOGS, SUBJECT_TELEMETRY_METRICS
from shared.event_envelope import build_event_envelope


async def emit_log_event(
    publisher,
    root_event_id: str,
    correlation_id: str,
    incident_id: str,
    phase: str,
    component: str,
    level: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    parent_event_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Emits a structured log event to autosre.telemetry.logs.v1.
    Note: Telemetry events carry context but do NOT advance the lifecycle parent chain.
    """
    payload = {
        "level": level.upper(),
        "message": message,
        "details": details or {},
        "emitted_at_ms": int(time.time() * 1000)
    }
    envelope = build_event_envelope(
        event_type="autosre.telemetry.logs",
        root_event_id=root_event_id,
        parent_event_id=parent_event_id,
        correlation_id=correlation_id,
        incident_id=incident_id,
        phase=phase,
        component=component,
        status="EMITTED",
        payload=payload,
        metrics={},
        source_engine="laptop2"
    )
    if publisher:
        try:
            return await publisher.publish_event(SUBJECT_TELEMETRY_LOGS, envelope)
        except Exception:
            pass
    return {"status": "SKIPPED"}


async def emit_metric_event(
    publisher,
    root_event_id: str,
    correlation_id: str,
    incident_id: str,
    phase: str,
    component: str,
    metric_name: str,
    value: float,
    unit: str = "ms",
    tags: Optional[Dict[str, Any]] = None,
    parent_event_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Emits a structured telemetry metric event to autosre.telemetry.metrics.v1.
    Note: Telemetry events carry context but do NOT advance the lifecycle parent chain.
    """
    payload = {
        "metric_name": metric_name,
        "value": float(value),
        "unit": unit,
        "tags": tags or {},
        "emitted_at_ms": int(time.time() * 1000)
    }
    envelope = build_event_envelope(
        event_type="autosre.telemetry.metrics",
        root_event_id=root_event_id,
        parent_event_id=parent_event_id,
        correlation_id=correlation_id,
        incident_id=incident_id,
        phase=phase,
        component=component,
        status="EMITTED",
        payload=payload,
        metrics={metric_name: float(value)},
        source_engine="laptop2"
    )
    if publisher:
        try:
            return await publisher.publish_event(SUBJECT_TELEMETRY_METRICS, envelope)
        except Exception:
            pass
    return {"status": "SKIPPED"}
