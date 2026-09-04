"""Shared package for AutoSRE cross-laptop subjects, envelope formatting, action registry, and event publishing."""

from shared.subjects import *
from shared.event_envelope import build_event_envelope, validate_event_envelope, compute_sha256, get_git_commit_sha
from shared.event_publisher import EventPublisher
from shared.telemetry import emit_log_event, emit_metric_event
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
    POLICY_ACTION_TO_CAPABILITY,
    CAPABILITY_TO_POLICY_ACTION,
    get_capability_for_policy_action,
    get_policy_action_for_intent,
    is_valid_policy_action,
)

__all__ = [
    "build_event_envelope",
    "validate_event_envelope",
    "compute_sha256",
    "get_git_commit_sha",
    "EventPublisher",
    "emit_log_event",
    "emit_metric_event",
    "ACTION_DATABASE_TUNE",
    "ACTION_RESTART_POD",
    "ACTION_SCALE_UP_REPLICAS",
    "ACTION_CONFIG_UPDATE",
    "ACTION_CIRCUIT_BREAK",
    "ACTION_FLUSH_CACHE",
    "ACTION_FLUSH_THREAD_POOL",
    "ACTION_FAILOVER_DB_REPLICA",
    "ACTION_NO_ACTION",
    "UNIFIED_POLICY_ACTIONS",
    "POLICY_ACTION_TO_CAPABILITY",
    "CAPABILITY_TO_POLICY_ACTION",
    "get_capability_for_policy_action",
    "get_policy_action_for_intent",
    "is_valid_policy_action",
]
