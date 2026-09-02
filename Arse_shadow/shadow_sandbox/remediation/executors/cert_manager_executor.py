import subprocess
from typing import Dict, Any
from .base import BaseExecutor

class CertManagerExecutor(BaseExecutor):
    """Executor for cert-manager / TLS certificate operations."""

    def execute(self, target: str, action: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"
        if action == "tls.certificate.renew":
            secret = parameters.get("secret_name")
            domain = parameters.get("domain")
            if not secret or not domain:
                return {"success": False, "target": shadow_target, "tool": action, "output": "ERROR: Missing secret_name or domain parameter"}
            
            cmd = ["kubectl", "cert-manager", "renew", secret]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if res.returncode == 0:
                    return {"success": True, "target": shadow_target, "tool": action, "output": f"SUCCESS: {res.stdout.strip()}"}
                else:
                    return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: cert-manager renewal failed: {res.stderr.strip()}"}
            except Exception as e:
                return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: cert-manager infrastructure unavailable: {str(e)}"}
        return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Unknown action {action}"}

