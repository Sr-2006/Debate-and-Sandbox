"""Unified Shared Action Registry and Capability Mapping for AutoSRE RL and Execution."""

from typing import Dict, List, Optional

# Shared Policy Action Vocabulary
ACTION_DATABASE_TUNE = "DATABASE_TUNE"
ACTION_RESTART_POD = "RESTART_POD"
ACTION_SCALE_UP_REPLICAS = "SCALE_UP_REPLICAS"
ACTION_CONFIG_UPDATE = "CONFIG_UPDATE"
ACTION_CIRCUIT_BREAK = "CIRCUIT_BREAK"
ACTION_FLUSH_CACHE = "FLUSH_CACHE"
ACTION_FLUSH_THREAD_POOL = "FLUSH_THREAD_POOL"
ACTION_FAILOVER_DB_REPLICA = "FAILOVER_DB_REPLICA"
ACTION_NO_ACTION = "NO_ACTION"

UNIFIED_POLICY_ACTIONS: List[str] = [
    ACTION_DATABASE_TUNE,
    ACTION_RESTART_POD,
    ACTION_SCALE_UP_REPLICAS,
    ACTION_CONFIG_UPDATE,
    ACTION_CIRCUIT_BREAK,
    ACTION_FLUSH_CACHE,
    ACTION_FLUSH_THREAD_POOL,
    ACTION_FAILOVER_DB_REPLICA,
    ACTION_NO_ACTION,
]

# Mapping from high-level RL Policy Action to concrete Execution Capability
POLICY_ACTION_TO_CAPABILITY: Dict[str, Optional[str]] = {
    ACTION_DATABASE_TUNE: "postgres.setting.update",
    ACTION_RESTART_POD: "k8s.pod.restart",
    ACTION_SCALE_UP_REPLICAS: "k8s.deployment.scale",
    ACTION_CONFIG_UPDATE: "k8s.configmap.update",
    ACTION_CIRCUIT_BREAK: "envoy.route.circuit_breaker",
    ACTION_FLUSH_CACHE: "redis.cache.flush",
    ACTION_FLUSH_THREAD_POOL: "tomcat.threadpool.drain",
    ACTION_FAILOVER_DB_REPLICA: "postgres.replica.failover",
    ACTION_NO_ACTION: None,
}

# Reverse mapping from concrete Execution Capability / Intent Type to RL Policy Action
CAPABILITY_TO_POLICY_ACTION: Dict[str, str] = {
    "postgres.setting.update": ACTION_DATABASE_TUNE,
    "k8s.pod.restart": ACTION_RESTART_POD,
    "container.restart": ACTION_RESTART_POD,
    "k8s.deployment.scale": ACTION_SCALE_UP_REPLICAS,
    "k8s.configmap.update": ACTION_CONFIG_UPDATE,
    "envoy.route.circuit_breaker": ACTION_CIRCUIT_BREAK,
    "redis.cache.flush": ACTION_FLUSH_CACHE,
    "redis.eviction_policy.update": ACTION_FLUSH_CACHE,
    "tomcat.threadpool.drain": ACTION_FLUSH_THREAD_POOL,
    "postgres.replica.failover": ACTION_FAILOVER_DB_REPLICA,
    "observe.logs.search": ACTION_NO_ACTION,
    "NO_SUPPORTED_ACTION": ACTION_NO_ACTION,
}


def get_capability_for_policy_action(policy_action: str) -> Optional[str]:
    """Returns the concrete execution capability mapped to a policy action, or None."""
    return POLICY_ACTION_TO_CAPABILITY.get(policy_action)


def get_policy_action_for_intent(intent_type: str) -> str:
    """Maps an intent/capability name to the standardized policy action, defaulting to NO_ACTION."""
    if not intent_type:
        return ACTION_NO_ACTION
    return CAPABILITY_TO_POLICY_ACTION.get(intent_type, ACTION_NO_ACTION)


def is_valid_policy_action(action: str) -> bool:
    """Checks if an action string is part of the unified policy action vocabulary."""
    return action in UNIFIED_POLICY_ACTIONS
