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
        """Translates problem description and action commands into a typed executor proposal."""
        if not action_commands:
            return {
                "tool": None,
                "unmapped": True,
                "reason": "Empty action commands provided"
            }

        target = self.extract_target_service(problem_text)
        commands_str = " ".join(action_commands).lower()
        combined_text = f"{problem_text} {commands_str}".lower()

        if any(kw in combined_text for kw in ["lock_timeout", "statement_timeout", "max_connections", "alter system"]):
            intent_type = "postgres.setting.update"
            executor_name = "postgres_executor"
            verifier_name = "postgres_verifier"
            params = {"setting_name": "max_connections", "value": 200}
        elif "eviction" in combined_text or "redis" in combined_text:
            intent_type = "redis.eviction_policy.update"
            executor_name = "redis_executor"
            verifier_name = "redis_verifier"
            params = {"policy": "volatile-lru"}
        elif "scale" in combined_text or "replicas" in combined_text:
            intent_type = "workload.replicas.scale"
            executor_name = "kubernetes_executor"
            verifier_name = "kubernetes_verifier"
            params = {"replicas": 3}
        elif "cpu" in combined_text or "memory" in combined_text or "throttle" in combined_text:
            intent_type = "workload.resources.patch"
            executor_name = "kubernetes_executor"
            verifier_name = "kubernetes_verifier"
            params = {"resource_type": "cpu", "limit_value": "2.0"}
        elif "cert" in combined_text or "tls" in combined_text:
            intent_type = "tls.certificate.renew"
            executor_name = "cert_manager_executor"
            verifier_name = "tls_verifier"
            params = {"secret_name": "tls-secret", "domain": "api.example.com"}
        elif "cilium" in combined_text or "bpf" in combined_text:
            intent_type = "cilium.policy.reload"
            executor_name = "cilium_executor"
            verifier_name = "network_verifier"
            params = {"policy_name": "ingress-policy"}
        elif "ceph" in combined_text or "storage" in combined_text:
            intent_type = "ceph.health.inspect"
            executor_name = "ceph_executor"
            verifier_name = "storage_verifier"
            params = {}
        elif "drain" in combined_text or "cordon" in combined_text:
            intent_type = "node.cordon"
            executor_name = "kubernetes_executor"
            verifier_name = "kubernetes_verifier"
            params = {"node_name": target}
        else:
            intent_type = "container.restart"
            executor_name = "docker_executor"
            verifier_name = "service_health"
            params = {}

        return {
            "tool": intent_type,
            "executor_name": executor_name,
            "verifier_name": verifier_name,
            "target": target,
            "parameters": params,
            "reasoning": f"Mapped '{commands_str}' to catalog operation {intent_type}"
        }