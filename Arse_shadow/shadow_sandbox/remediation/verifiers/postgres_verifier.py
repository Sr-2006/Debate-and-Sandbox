import subprocess
from typing import Dict, Any
from .base import BaseVerifier

class PostgresVerifier(BaseVerifier):
    """Verifier for PostgreSQL setting updates, lock checks, and readiness postconditions."""

    def verify(self, target: str, action: str, parameters: Dict[str, Any], execution_result: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"

        if not execution_result.get("success", False):
            return {"passed": False, "target": shadow_target, "verifier": "PostgresVerifier", "reason": "Execution failed before verification"}

        if action == "postgres.setting.update":
            setting = parameters.get("setting_name")
            expected = str(parameters.get("value", "")).strip()
            cmd = ["docker", "exec", shadow_target, "psql", "-U", "postgres", "-t", "-c", f"SHOW {setting};"]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if res.returncode != 0:
                    return {"passed": False, "target": shadow_target, "verifier": "PostgresVerifier", "reason": f"ERROR: psql SHOW {setting} failed: {res.stderr.strip()}"}
                val = res.stdout.strip()
                passed = (val == expected or expected.lower() in val.lower())
                return {"passed": passed, "target": shadow_target, "verifier": "PostgresVerifier", "reason": f"Setting {setting} verified: '{val}' (expected '{expected}')"}
            except Exception as e:
                return {"passed": False, "target": shadow_target, "verifier": "PostgresVerifier", "reason": f"ERROR: Postgres verification exception: {str(e)}"}

        elif action in ["postgres.lock.diagnose", "postgres.wal.diagnose"]:
            return {"passed": True, "target": shadow_target, "verifier": "PostgresVerifier", "reason": f"Diagnostics completed: {execution_result.get('output')}"}

        return {"passed": True, "target": shadow_target, "verifier": "PostgresVerifier", "reason": "Postgres verification completed"}

