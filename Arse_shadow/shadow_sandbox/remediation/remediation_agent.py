import json
import re
from typing import Dict, Any, List, Optional
from shadow_sandbox.remediation.policy_engine import PolicyEngine
from shadow_sandbox.remediation.executors import (
    DockerExecutor, PostgresExecutor, RedisExecutor, KubernetesExecutor,
    CertManagerExecutor, CiliumExecutor, CephExecutor
)
from shadow_sandbox.remediation.verifiers import (
    ServiceHealthVerifier, PostgresVerifier, RedisVerifier,
    KubernetesVerifier, TLSVerifier, NetworkVerifier, StorageVerifier
)

class BoundedRemediationAgent:
    """Remediation Agent that maps v2 typed intents (or legacy action commands) to registered executors."""

    KNOWN_SERVICES = [
        "postgres-db", "redis", "rabbitmq", "api-gateway",
        "auth-service", "order-service", "payment-service",
        "user-service", "ingress-gateway", "ceph-storage"
    ]

    def __init__(self):
        self.policy_engine = PolicyEngine()
        self.executors = {
            "docker_executor": DockerExecutor(),
            "postgres_executor": PostgresExecutor(),
            "redis_executor": RedisExecutor(),
            "kubernetes_executor": KubernetesExecutor(),
            "cert_manager_executor": CertManagerExecutor(),
            "cilium_executor": CiliumExecutor(),
            "ceph_executor": CephExecutor(),
        }
        self.verifiers = {
            "service_health": ServiceHealthVerifier(),
            "postgres_verifier": PostgresVerifier(),
            "redis_verifier": RedisVerifier(),
            "kubernetes_verifier": KubernetesVerifier(),
            "tls_verifier": TLSVerifier(),
            "network_verifier": NetworkVerifier(),
            "storage_verifier": StorageVerifier(),
        }

    def extract_target_service(self, problem_text: str) -> str:
        """Extracts target service name from problem description text."""
        match = re.search(r"Target Service:\s*`([^`]+)`", problem_text, re.IGNORECASE)
        if match:
            raw_target = match.group(1).strip()
        else:
            p_lower = problem_text.lower()
            if "postgres" in p_lower:
                raw_target = "postgres-db"
            elif "redis" in p_lower:
                raw_target = "redis"
            elif "rabbitmq" in p_lower:
                raw_target = "rabbitmq"
            else:
                raw_target = "api-gateway"
                for svc in self.KNOWN_SERVICES:
                    if svc in p_lower:
                        raw_target = svc
                        break

        if not raw_target.startswith("shadow-"):
            raw_target = f"shadow-{raw_target}"

        return raw_target

    def propose_action(self, problem_text: str, action_commands: List[str]) -> Dict[str, Any]:
        """Translates problem description and action commands into a typed executor proposal using capabilities.yaml."""
        if not action_commands:
            return {
                "tool": None,
                "unmapped": True,
                "reason": "Empty action commands provided"
            }

        target = self.extract_target_service(problem_text)
        first_cmd = str(action_commands[0]).strip()

        # Parse intent_type and parameters if provided as "intent_type: {json_params}" or raw intent_type
        intent_type = first_cmd
        params = {}
        if ":" in first_cmd and ("{" in first_cmd or "}" in first_cmd or "'" in first_cmd or '"' in first_cmd):
            parts = first_cmd.split(":", 1)
            intent_type = parts[0].strip()
            param_str = parts[1].strip().replace("'", '"')
            try:
                params = json.loads(param_str)
            except Exception:
                params = {}

        capabilities = self.policy_engine.capabilities
        if intent_type in capabilities:
            cap_def = capabilities[intent_type]
            executor_name = cap_def.get("executor", "docker_executor")
            verifier_name = cap_def.get("verifier", "service_health")
            return {
                "tool": intent_type,
                "executor_name": executor_name,
                "verifier_name": verifier_name,
                "target": target,
                "parameters": params,
                "unmapped": False,
                "reasoning": f"Resolved catalog operation {intent_type}"
            }

        # Fail closed for unknown or unregistered intents
        return {
            "tool": intent_type,
            "executor_name": None,
            "verifier_name": None,
            "target": target,
            "parameters": params,
            "unmapped": True,
            "reason": f"Unknown capability '{intent_type}' not found in catalog"
        }