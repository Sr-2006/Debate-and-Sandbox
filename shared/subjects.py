"""Canonical NATS JetStream Subjects for AutoSRE Cross-Laptop Event Pipeline."""

STREAM_NAME = "AUTOSRE"

# Canonical Laptop 1 & Laptop 2 Pipeline Subjects
SUBJECT_INCIDENT_READY = "autosre.incident.ready.v1"
SUBJECT_TRANSPORT_RECEIVED = "autosre.transport.received.v1"

# Phase 3 subjects
SUBJECT_PHASE3_STARTED = "autosre.phase3.started.v1"
SUBJECT_PHASE3_DEBATE = "autosre.phase3.debate.v1"
SUBJECT_PHASE3_COMPLETED = "autosre.phase3.completed.v1"

# RL Advisory (Laptop 2)
SUBJECT_RL_LAPTOP2_ADVISORY = "autosre.rl.laptop2.advisory.v1"

# Phase 4 subjects
SUBJECT_PHASE4_STARTED = "autosre.phase4.started.v1"
SUBJECT_PHASE4_ATTESTATION = "autosre.phase4.attestation.v1"
SUBJECT_PHASE4_EXECUTION = "autosre.phase4.execution.v1"
SUBJECT_PHASE4_VERIFICATION = "autosre.phase4.verification.v1"
SUBJECT_PHASE4_COMPLETED = "autosre.phase4.completed.v1"

# RL Feedback (Laptop 2)
SUBJECT_RL_FEEDBACK = "autosre.rl.feedback.v1"

# Pipeline terminal subjects
SUBJECT_PIPELINE_COMPLETED = "autosre.pipeline.completed.v1"
SUBJECT_PIPELINE_FAILED = "autosre.pipeline.failed.v1"

# Telemetry subjects
SUBJECT_TELEMETRY_LOGS = "autosre.telemetry.logs.v1"
SUBJECT_TELEMETRY_METRICS = "autosre.telemetry.metrics.v1"

# System & Heartbeat subjects
SUBJECT_LAPTOP2_HEARTBEAT = "autosre.system.laptop2.heartbeat.v1"

# Legacy compatibility
SUBJECT_LEGACY_COMPLETED = "autosre.phase34.completed.v1"
