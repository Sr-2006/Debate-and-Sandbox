import subprocess
from typing import Dict, Any
from .base import BaseExecutor

class CiliumExecutor(BaseExecutor):
    """Executor for Cilium eBPF network policy operations."""

    def execute(self, target: str, action: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"
        if action == "cilium.policy.inspect":
            cmd = ["cilium", "status"]
        elif action == "cilium.policy.reload":
            policy = parameters.get("policy_name")
            if not policy:
                return {"success": False, "target": shadow_target, "tool": action, "output": "ERROR: Missing policy_name parameter"}
            cmd = ["cilium", "policy", "import", policy]
        else:
            return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Unknown action {action}"}

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                return {"success": True, "target": shadow_target, "tool": action, "output": f"SUCCESS: {res.stdout.strip()}"}
            else:
                return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Cilium CLI failed ({res.stderr.strip()})"}
        except Exception as e:
            return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Cilium infrastructure unavailable: {str(e)}"}

