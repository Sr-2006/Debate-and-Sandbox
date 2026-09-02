import sys
import os
import subprocess
from typing import Dict, Any
from .base import BaseExecutor

class RedisExecutor(BaseExecutor):
    """Executor for Redis cache configuration operations strictly using live Docker container."""

    def execute(self, target: str, action: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"

        if action == "redis.eviction_policy.read":
            cmd = ["docker", "exec", shadow_target, "redis-cli", "CONFIG", "GET", "maxmemory-policy"]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if res.returncode == 0:
                    return {"success": True, "target": shadow_target, "tool": action, "output": res.stdout.strip()}
                else:
                    return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: {res.stderr.strip()}"}
            except Exception as e:
                return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Redis command failed: {str(e)}"}

        elif action == "redis.eviction_policy.update":
            policy = parameters.get("policy", "volatile-lru")
            cmd = ["docker", "exec", shadow_target, "redis-cli", "CONFIG", "SET", "maxmemory-policy", policy]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if res.returncode == 0:
                    return {"success": True, "target": shadow_target, "tool": action, "output": f"SUCCESS: maxmemory-policy set to {policy}"}
                else:
                    return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: {res.stderr.strip()}"}
            except Exception as e:
                return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Redis command failed: {str(e)}"}

        return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Unknown action {action}"}


class MockRedisExecutor(BaseExecutor):
    """Explicit mock adapter for simulated Redis operations."""

    def execute(self, target: str, action: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"
        if action == "redis.eviction_policy.read":
            return {"success": True, "target": shadow_target, "tool": action, "output": "SUCCESS: maxmemory-policy volatile-lru"}
        elif action == "redis.eviction_policy.update":
            policy = parameters.get("policy", "allkeys-lru")
            return {"success": True, "target": shadow_target, "tool": action, "output": f"SUCCESS: maxmemory-policy set to {policy}"}
        return {"success": True, "target": shadow_target, "tool": action, "output": "SUCCESS: ok"}


