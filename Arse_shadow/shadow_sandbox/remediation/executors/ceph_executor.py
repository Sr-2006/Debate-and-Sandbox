import subprocess
from typing import Dict, Any
from .base import BaseExecutor

class CephExecutor(BaseExecutor):
    """Executor for Ceph read-only diagnostics and snapshot restore operations."""

    def execute(self, target: str, action: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"
        if action == "ceph.health.inspect":
            cmd = ["ceph", "health"]
        elif action == "storage.snapshot.restore":
            snap = parameters.get("snapshot_id")
            if not snap:
                return {"success": False, "target": shadow_target, "tool": action, "output": "ERROR: Missing snapshot_id parameter"}
            cmd = ["rbd", "snap", "rollback", snap]
        else:
            return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Unknown action {action}"}

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                return {"success": True, "target": shadow_target, "tool": action, "output": f"SUCCESS: {res.stdout.strip()}"}
            else:
                return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Ceph command failed ({res.stderr.strip()})"}
        except Exception as e:
            return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Ceph storage infrastructure unavailable: {str(e)}"}

