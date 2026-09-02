import sys
import os
import docker
from typing import Dict, Any
from .base import BaseExecutor


class DockerExecutor(BaseExecutor):
    """Executor for Docker container management operations strictly using live Docker daemon."""

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

    def inspect_container(self, target: str) -> Dict[str, Any]:
        """Captures actual Docker container ID, status, health, and restart count before and after."""
        if not self.client:
            return {"success": False, "error": "Docker daemon unavailable"}
        try:
            container = self.client.containers.get(target)
            attrs = container.attrs or {}
            state = attrs.get("State", {})
            return {
                "success": True,
                "target": target,
                "container_id": container.id[:12],
                "status": container.status,
                "health": state.get("Health", {}).get("Status", "none"),
                "restart_count": attrs.get("RestartCount", 0)
            }
        except Exception as e:
            return {"success": False, "target": target, "error": str(e)}

    def _observe_container(self, target: str, action: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        if not self.client:
            return {"success": False, "target": target, "tool": action, "output": f"ERROR: Docker daemon unavailable for observation {action}"}
        try:
            container = self.client.containers.get(target)
            logs = container.logs(tail=parameters.get("max_lines", 50)).decode("utf-8", errors="ignore")
            return {"success": True, "target": target, "tool": action, "output": f"OBSERVED: {logs[:500]}"}
        except Exception as e:
            return {"success": False, "target": target, "tool": action, "output": f"ERROR: Failed to observe container {target}: {str(e)}"}


class MockDockerExecutor(BaseExecutor):
    """Explicit mock adapter for simulated Docker container operations."""

    def execute(self, target: str, action: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"
        if action == "container.restart":
            return {"success": True, "target": shadow_target, "tool": "container.restart", "output": f"SUCCESS (SIMULATED): Container {shadow_target} restarted"}
        elif action in ["observe.logs.search", "observe.metrics.query"]:
            return {"success": True, "target": shadow_target, "tool": action, "output": f"OBSERVED (SIMULATED): logs for {shadow_target}"}
        return {"success": False, "target": shadow_target, "tool": action, "output": "ERROR: Unsupported action"}

    def inspect_container(self, target: str) -> Dict[str, Any]:
        return {"success": True, "target": target, "container_id": "c1234567890a", "status": "running", "health": "healthy", "restart_count": 1}



