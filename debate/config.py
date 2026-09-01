import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Global configuration for the 6GB-VRAM laptop-safe Multi-Agent Debate Engine.
# Worker agents stay on 3B for throughput and memory safety; the orchestrator is
# intentionally upgraded to 7B so the synthesis layer remains strong without
# running all models concurrently in the same memory budget.

WORKER_MODEL = os.environ.get("WORKER_MODEL", "qwen2.5:3b")
MODEL_NAME = WORKER_MODEL
ORCHESTRATOR_MODEL = os.environ.get("ORCHESTRATOR_MODEL", "qwen2.5:7b")
OLLAMA_API_URL = "http://localhost:11434/api/chat"

# Ollama GPU VRAM Warmup Payload (Keeps model resident for 30 mins)
OLLAMA_WARMUP_PAYLOAD = {
    "model": MODEL_NAME,
    "messages": [{"role": "user", "content": "ping"}],
    "options": {"num_ctx": 1024, "temperature": 0.0},
    "keep_alive": "30m",
    "stream": False
}

# Per-agent temperature strategy
OPTIMIST_TEMP = 0.3      # Bounded recovery options
CRITIC_TEMP = 0.1        # Strict risk & failure mode analysis
FACT_CHECKER_TEMP = 0.0  # Pure deterministic technical verification
ORCHESTRATOR_TEMP = 0.1  # Highly controlled consensus synthesis

# Buffered Token Caps (raised from 160/260 — the old caps caused mid-value JSON
# truncation, which was the root cause of the parse-fallback bug)
MAX_AGENT_TOKENS = 260
MAX_ORCHESTRATOR_TOKENS = 320

# Model tiering: cheap fast model for parallel agents, bigger brain for synthesis.
# The runtime budget is intentionally tightened for 6GB VRAM laptops.
NUM_CTX = 1536
KEEP_ALIVE = "30m"
OLLAMA_NUM_PARALLEL = 1
REQUEST_TIMEOUT = 300.0  # 5-minute HTTP timeout window to prevent queue drops under high load

# --- Final_v1 runtime policy (6GB-VRAM laptop build) ---
WORKER_CONTEXT_LIMIT = 1024
ORCHESTRATOR_CONTEXT_LIMIT = 768
MAX_PARALLEL_WORKERS = 1
EVIDENCE_GROUNDING_MIN = 0.25
MAX_AUTONOMOUS_CONFIDENCE = 85
SANDBOX_CONFIDENCE_FLOOR = 65
HIGH_RISK_ACTION_PATTERNS = [
    "delete",
    "drop",
    "rm -rf",
    "truncate",
    "purge",
    "flushall",
    "kill -9",
    "format",
    "mkfs",
    "reset --hard",
]

# Layer 1: Absolute Nuclear Command Regex Patterns
ABSOLUTE_DESTRUCTIVE_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bflushall\b",
    r"\bdrop\s+(database|table|schema)\b",
    r"\bprune\s+-a\b",
    r"\bscale\s+.*--replicas=0\b",
    r"\btruncate\s+table\b",
    r"\bkill\s+-9\s+1\b",
    r"\bmkfs\b",
    r"\bformat\s+[c-z]:\b"
]

# Layer 2: Scoped Destructive Command Regex Patterns
SCOPED_DESTRUCTIVE_PATTERNS = [
    r"\bdelete\s+(namespace|ns|pod|deployment|service|node|all)\b",
    r"\brm\s+-r?\s+/(var|etc|usr|bin|root|data)\b",
    r"\bkubectl\s+delete\b",
    r"\bdocker\s+(rm|rmi|system\s+prune)\b",
    r"\bdropdb\b"
]

# Layer 3: Semantic Centroids (Forbidden Destructive Intents)
FORBIDDEN_INTENTS = [
    "delete kubernetes namespace prod",
    "remove all files recursively from disk",
    "drop production database table",
    "flush all keys in redis cache",
    "scale deployment replicas to zero",
    "purge persistent storage volume",
    "terminate running system process",
    "destroy cluster state completely"
]

SEMANTIC_VETO_THRESHOLD = 0.82
SEMANTIC_THRESHOLD = 0.70

# --- Scoring v2 Weights ---
W_COMPONENT_AGREEMENT = 30   # all agents + orchestrator name the same primary component
W_EVIDENCE_GROUNDING = 30    # agent RCA cites real telemetry anchors
W_ACTIONABILITY = 20         # safe, executable, evidence-backed commands
W_STRUCTURE = 10             # required orchestrator fields present
W_SCHEMA_BONUS = 10          # micro-CoT discipline + valid JSON across agents
PENALTY_UNGROUNDED_CMD = 15  # command keywords absent from telemetry evidence
PENALTY_DIVERGENCE = 10      # orchestrator component unsupported by any agent
PENALTY_PARSE_FAILURE = 20   # penalty per parse fallback / corrupted agent response

COMPONENT_VOCAB = ["network", "disk", "memory", "cpu", "dns", "config", "code", "database", "dependency", "unknown"]

# Executability validation
KNOWN_BINARIES = {
    "kubectl", "systemctl", "docker", "ip", "tc", "chronyc", "prometheus",
    "kill", "restart", "service", "curl", "set", "etcdctl", "envoy",
    "redis-cli", "ceph-volume", "journalctl", "dmesg", "ss", "netstat",
    "iptables", "nft", "ping", "traceroute", "dig", "nslookup", "ps",
    "top", "htop", "free", "df", "du", "ls", "cat", "grep", "awk", "sed",
}

# Difficulty prior scaling
DIFFICULTY_MAX_PENALTY = 12  # max points deducted for hardest incidents

LATENCY_TARGET = 20.0
LATENCY_GRACE_PERIOD = 2.0

AUTONOMOUS_THRESHOLD = 85
SANDBOX_THRESHOLD = 65

# System prompt file paths
OPTIMIST_PROMPT = BASE_DIR / "prompts" / "optimist.txt"
CRITIC_PROMPT = BASE_DIR / "prompts" / "critic.txt"
FACT_CHECKER_PROMPT = BASE_DIR / "prompts" / "fact_checker.txt"
ORCHESTRATOR_PROMPT = BASE_DIR / "prompts" / "orchestrator.txt"
