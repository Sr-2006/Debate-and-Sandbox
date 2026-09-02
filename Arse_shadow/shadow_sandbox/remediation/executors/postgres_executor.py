import re
import subprocess
from typing import Dict, Any
from .base import BaseExecutor

ALLOWED_SETTINGS = {
    "max_connections", "lock_timeout", "statement_timeout", "work_mem",
    "shared_buffers", "idle_in_transaction_session_timeout", "wal_level",
    "archive_cleanup_command", "max_worker_processes", "autovacuum_vacuum_scale_factor"
}

class PostgresExecutor(BaseExecutor):
    """Executor for PostgreSQL database operations."""

    def execute(self, target: str, action: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        shadow_target = target if target.startswith("shadow-") else f"shadow-{target}"

        if action == "postgres.setting.update":
            setting = parameters.get("setting_name")
            value = parameters.get("value")
            if not setting or value is None:
                return {"success": False, "target": shadow_target, "tool": action, "output": "ERROR: Missing setting_name or value"}

            if setting not in ALLOWED_SETTINGS:
                return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Setting '{setting}' is not in allowed settings allowlist"}

            # Regex validation to prevent SQL injection in setting value
            str_val = str(value).strip()
            if not re.match(r"^[a-zA-Z0-9_\.\-]+$", str_val):
                return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Setting value '{str_val}' failed strict format validation"}

            # Query with validated setting name and sanitized value
            query = f"ALTER SYSTEM SET {setting} = '{str_val}'; SELECT pg_reload_conf();"
            return self._run_sql(shadow_target, query, action)

        elif action == "postgres.lock.diagnose":
            query = "SELECT blocking.pid AS blocking_pid, blocked.pid AS blocked_pid, blocked.query FROM pg_stat_activity blocked JOIN pg_locks blockeda ON blocked.pid = blockeda.pid JOIN pg_locks blockinga ON blockeda.locktype = blockinga.locktype AND blockeda.database IS NOT DISTINCT FROM blockinga.database AND blockeda.relation IS NOT DISTINCT FROM blockinga.relation AND blockeda.page IS NOT DISTINCT FROM blockinga.page AND blockeda.tuple IS NOT DISTINCT FROM blockinga.tuple AND blockeda.virtualxid IS NOT DISTINCT FROM blockinga.virtualxid AND blockeda.transactionid IS NOT DISTINCT FROM blockinga.transactionid AND blockeda.classid IS NOT DISTINCT FROM blockinga.classid AND blockeda.objid IS NOT DISTINCT FROM blockinga.objid AND blockeda.objsubid IS NOT DISTINCT FROM blockinga.objsubid AND blockeda.pid != blockinga.pid JOIN pg_stat_activity blocking ON blockinga.pid = blocking.pid WHERE NOT blockeda.granted;"
            return self._run_sql(shadow_target, query, action)

        elif action == "postgres.wal.diagnose":
            query = "SELECT archived_count, failed_count, last_archived_wal, last_archived_time FROM pg_stat_archiver;"
            return self._run_sql(shadow_target, query, action)

        elif action == "postgres.wal.archive_cleanup":
            retention = parameters.get("retention_boundary", "000000010000000000000001")
            if not re.match(r"^[a-zA-Z0-9]+$", str(retention)):
                return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Invalid retention boundary format: {retention}"}
            query = f"SELECT pg_walfile_name(pg_current_wal_lsn());"
            return self._run_sql(shadow_target, query, action)

        return {"success": False, "target": shadow_target, "tool": action, "output": f"ERROR: Unknown action {action}"}

    def _run_sql(self, target: str, query: str, action: str) -> Dict[str, Any]:
        cmd = ["docker", "exec", target, "psql", "-U", "postgres", "-c", query]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                return {"success": True, "target": target, "tool": action, "output": f"SUCCESS: {res.stdout.strip()}"}
            else:
                return {"success": False, "target": target, "tool": action, "output": f"ERROR: {res.stderr.strip()}"}
        except Exception as e:
            return {"success": False, "target": target, "tool": action, "output": f"ERROR: Postgres SQL execution failed: {str(e)}"}


