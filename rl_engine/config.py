import os

RL_ENABLED = os.environ.get("RL_ENABLED", "true").lower() == "true"
RL_OPERATING_MODE = os.environ.get("RL_OPERATING_MODE", "SHADOW")  # SHADOW | ADVISORY
RL_POLICY_NAME = os.environ.get("RL_POLICY_NAME", "safe_disjoint_linucb")
RL_POLICY_VERSION = os.environ.get("RL_POLICY_VERSION", "rl-mvp-1")
RL_FEATURE_VERSION = os.environ.get("RL_FEATURE_VERSION", "features-v1")
RL_REWARD_VERSION = os.environ.get("RL_REWARD_VERSION", "reward-v1")
RL_ALPHA = float(os.environ.get("RL_ALPHA", "0.25"))
RL_MIN_REAL_EPISODES = int(os.environ.get("RL_MIN_REAL_EPISODES", "100"))
RL_MIN_CAPABILITY_EPISODES = int(os.environ.get("RL_MIN_CAPABILITY_EPISODES", "20"))
RL_TRAIN_EVERY_NEW_EPISODES = int(os.environ.get("RL_TRAIN_EVERY_NEW_EPISODES", "25"))
RL_MODEL_DIR = os.environ.get("RL_MODEL_DIR", "rl_engine/models")
RL_FAIL_OPEN_TO_EXISTING_PIPELINE = os.environ.get("RL_FAIL_OPEN_TO_EXISTING_PIPELINE", "true").lower() == "true"
RL_LAPTOP1_TRANSPORT = os.environ.get("RL_LAPTOP1_TRANSPORT", "disabled")  # disabled | file | nats
RL_OUTBOX_DIR = os.environ.get("RL_OUTBOX_DIR", "rl_engine/outbox")

CATEGORICAL_VOCAB = {
    "capabilities": ["container.restart", "postgres.setting.update", "redis.eviction_policy.update", "observe.logs.search", "OTHER"],
    "target_kinds": ["container", "service", "database", "cache", "workload", "node", "storage", "OTHER"],
    "modes": ["OBSERVE", "SIMULATE", "MUTATE_REVERSIBLE", "MUTATE_HIGH_RISK", "OTHER"],
    "risk_classes": ["LOW", "MEDIUM", "HIGH", "CRITICAL", "OTHER"],
    "execution_tiers": ["tier_1", "tier_2", "tier_3", "failed", "OTHER"]
}

ROUTING_ACTIONS = ["ACCEPT_PROPOSAL", "OBSERVE_FIRST", "REQUIRE_HUMAN_REVIEW", "ABSTAIN"]
