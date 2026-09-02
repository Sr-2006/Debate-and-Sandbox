import docker
from typing import Dict, Any
from .base import BaseVerifier

class ServiceHealthVerifier(BaseVerifier):
    """Verifier for general container service health & readiness postconditions."""

    def __init__(self):
        try:
            self.client = docker.from_env()
        except Exception:
            self.client = None

    def verify(self, target: str, action: str, parameters: Dict[str, Any], execution_result: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"
        
        if not execution_result.get("success", False):
            return {"passed": False, "target": shadow_target, "verifier": "ServiceHealthVerifier", "reason": "Execution failed before verification"}

        if not self.client:
            return {"passed": False, "target": shadow_target, "verifier": "ServiceHealthVerifier", "reason": "ERROR: Docker daemon unavailable for health check"}

        try:
            container = self.client.containers.get(shadow_target)
            is_running = container.status == "running"
            return {"passed": is_running, "target": shadow_target, "verifier": "ServiceHealthVerifier", "reason": f"Container status: {container.status}"}
        except Exception as e:
            return {"passed": False, "target": shadow_target, "verifier": "ServiceHealthVerifier", "reason": f"ERROR: Service health check failed: {str(e)}"}

