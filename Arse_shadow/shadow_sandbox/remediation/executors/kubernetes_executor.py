import subprocess
from typing import Dict, Any
from .base import BaseExecutor

class KubernetesExecutor(BaseExecutor):
    """Executor for Kubernetes workload, replica, node, and ingress operations."""

    def execute(self, target: str, action: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"

        cmd = None
        if action == "workload.replicas.scale":
            replicas = parameters.get("replicas", 3)
            cmd = ["kubectl", "scale", f"deployment/{shadow_target}", f"--replicas={replicas}"]

        elif action == "workload.resources.patch":
            res_type = parameters.get("resource_type", "cpu")
            limit = parameters.get("limit_value", "2.0")
            patch_json = f'{{"spec":{{"template":{{"spec":{{"containers":[{{"name":"{shadow_target}","resources":{{"limits":{{"{res_type}":"{limit}"}}}}}}]}}}}}}}}'
            cmd = ["kubectl", "patch", "deployment", shadow_target, "-p", patch_json]

        elif action in ["workload.rollout.restart", "grpc.workload.restart"]:
            cmd = ["kubectl", "rollout", "restart", f"deployment/{shadow_target}"]

        elif action == "ingress.rate_limit.patch":
            rps = parameters.get("rate_limit_rps", 100)
            cmd = ["kubectl", "annotate", "ingress", shadow_target, f"nginx.ingress.kubernetes.io/limit-rps={rps}", "--overwrite"]

        elif action == "node.cordon":
            node = parameters.get("node_name", shadow_target)
            cmd = ["kubectl", "cordon", node]

        elif action == "node.drain":
            node = parameters.get("node_name", shadow_target)
            cmd = ["kubectl", "drain", node, "--ignore-daemonsets", "--delete-emptydir-data"]

        if not cmd:
            return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Unknown action {action}"}

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                return {"success": True, "target": shadow_target, "tool": action, "output": f"SUCCESS: {res.stdout.strip()}"}
            else:
                return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: kubectl failed ({res.stderr.strip()})"}
        except Exception as e:
            return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Kubernetes infrastructure unavailable: {str(e)}"}

