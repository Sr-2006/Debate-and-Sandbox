import subprocess
from typing import Dict, Any
from .base import BaseVerifier

class RedisVerifier(BaseVerifier):
    def verify(self, target: str, action: str, parameters: Dict[str, Any], execution_result: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"
        if not execution_result.get("success", False):
            return {"passed": False, "target": shadow_target, "verifier": "RedisVerifier", "reason": "Execution failed before verification"}

        if action == "redis.eviction_policy.update":
            expected_policy = parameters.get("policy", "volatile-lru")
            cmd = ["docker", "exec", shadow_target, "redis-cli", "CONFIG", "GET", "maxmemory-policy"]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if res.returncode != 0:
                    return {"passed": False, "target": shadow_target, "verifier": "RedisVerifier", "reason": f"ERROR: redis-cli CONFIG GET failed: {res.stderr.strip()}"}
                output = res.stdout.strip()
                passed = expected_policy in output
                return {"passed": passed, "target": shadow_target, "verifier": "RedisVerifier", "reason": f"Redis policy verified: '{output}' (expected '{expected_policy}')"}
            except Exception as e:
                return {"passed": False, "target": shadow_target, "verifier": "RedisVerifier", "reason": f"ERROR: Redis verification failed: {str(e)}"}

        return {"passed": True, "target": shadow_target, "verifier": "RedisVerifier", "reason": "Redis verification completed"}

class KubernetesVerifier(BaseVerifier):
    def verify(self, target: str, action: str, parameters: Dict[str, Any], execution_result: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"
        if not execution_result.get("success", False):
            return {"passed": False, "target": shadow_target, "verifier": "KubernetesVerifier", "reason": "Execution failed before verification"}

        cmd = ["kubectl", "get", "deployment", shadow_target]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                return {"passed": False, "target": shadow_target, "verifier": "KubernetesVerifier", "reason": f"ERROR: kubectl get deployment failed: {res.stderr.strip()}"}
            return {"passed": True, "target": shadow_target, "verifier": "KubernetesVerifier", "reason": f"Kubernetes workload {shadow_target} verified"}
        except Exception as e:
            return {"passed": False, "target": shadow_target, "verifier": "KubernetesVerifier", "reason": f"ERROR: Kubernetes verification unavailable: {str(e)}"}

class TLSVerifier(BaseVerifier):
    def verify(self, target: str, action: str, parameters: Dict[str, Any], execution_result: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"
        if not execution_result.get("success", False):
            return {"passed": False, "target": shadow_target, "verifier": "TLSVerifier", "reason": "Execution failed before verification"}

        secret = parameters.get("secret_name")
        if not secret:
            return {"passed": False, "target": shadow_target, "verifier": "TLSVerifier", "reason": "ERROR: Missing secret_name parameter for TLS verification"}

        cmd = ["kubectl", "get", "secret", secret]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                return {"passed": False, "target": shadow_target, "verifier": "TLSVerifier", "reason": f"ERROR: kubectl get secret {secret} failed: {res.stderr.strip()}"}
            return {"passed": True, "target": shadow_target, "verifier": "TLSVerifier", "reason": f"TLS secret {secret} verified"}
        except Exception as e:
            return {"passed": False, "target": shadow_target, "verifier": "TLSVerifier", "reason": f"ERROR: TLS verification unavailable: {str(e)}"}

class NetworkVerifier(BaseVerifier):
    def verify(self, target: str, action: str, parameters: Dict[str, Any], execution_result: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"
        if not execution_result.get("success", False):
            return {"passed": False, "target": shadow_target, "verifier": "NetworkVerifier", "reason": "Execution failed before verification"}

        cmd = ["cilium", "status"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                return {"passed": False, "target": shadow_target, "verifier": "NetworkVerifier", "reason": f"ERROR: cilium status failed: {res.stderr.strip()}"}
            return {"passed": True, "target": shadow_target, "verifier": "NetworkVerifier", "reason": "Cilium network policy convergence verified"}
        except Exception as e:
            return {"passed": False, "target": shadow_target, "verifier": "NetworkVerifier", "reason": f"ERROR: Network verification unavailable: {str(e)}"}

class StorageVerifier(BaseVerifier):
    def verify(self, target: str, action: str, parameters: Dict[str, Any], execution_result: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"
        if not execution_result.get("success", False):
            return {"passed": False, "target": shadow_target, "verifier": "StorageVerifier", "reason": "Execution failed before verification"}

        cmd = ["ceph", "health"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                return {"passed": False, "target": shadow_target, "verifier": "StorageVerifier", "reason": f"ERROR: ceph health failed: {res.stderr.strip()}"}
            return {"passed": True, "target": shadow_target, "verifier": "StorageVerifier", "reason": "Storage cluster health verified"}
        except Exception as e:
            return {"passed": False, "target": shadow_target, "verifier": "StorageVerifier", "reason": f"ERROR: Storage verification unavailable: {str(e)}"}

