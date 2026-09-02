import docker
from typing import Dict, Any
from .base import BaseExecutor

class DockerExecutor(BaseExecutor):
    """Executor for Docker container management operations."""

    def __init__(self):
        try:
            self.client = docker.from_env()
        except Exception:
            self.client = None

    def execute(self, target: str, action: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"

        if action == "container.restart":
            return self._restart_container(shadow_target)
        elif action in ["observe.logs.search", "observe.metrics.query"]:
            return self._observe_container(shadow_target, action, parameters)
        else:
            return {
                "success": False,
                "target": shadow_target,
                "tool": action,
                "output": f"ERROR: Unsupported action '{action}' for DockerExecutor"
            }

    def _restart_container(self, target: str) -> Dict[str, Any]:
        if not self.client:
            return {"success": False, "target": target, "tool": "container.restart", "output": "ERROR: Docker daemon unavailable"}
        try:
            container = self.client.containers.get(target)
            container.restart(timeout=10)
            return {"success": True, "target": target, "tool": "container.restart", "output": f"SUCCESS: Container {target} restarted"}
        except Exception as e:
            return {"success": False, "target": target, "tool": "container.restart", "output": f"ERROR: {str(e)}"}

    def _observe_container(self, target: str, action: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        if not self.client:
            return {"success": False, "target": target, "tool": action, "output": f"ERROR: Docker daemon unavailable for observation {action}"}
        try:
            container = self.client.containers.get(target)
            logs = container.logs(tail=parameters.get("max_lines", 50)).decode("utf-8", errors="ignore")
            return {"success": True, "target": target, "tool": action, "output": f"OBSERVED: {logs[:500]}"}
        except Exception as e:
            return {"success": False, "target": target, "tool": action, "output": f"ERROR: Failed to observe container {target}: {str(e)}"}

