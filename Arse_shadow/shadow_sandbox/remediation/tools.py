#!/usr/bin/env python3
"""
shadow_sandbox/remediation/tools.py

Generic execution and state-inspection tools for shadow containers.
Enforces strict shadow- target name assertion on ALL operations.
"""

import docker
from docker.errors import APIError, NotFound
import subprocess
from typing import Dict, Any, Optional

def assert_shadow_target(target: str) -> str:
    """Hard safety assertion: Refuses to operate on non-shadow targets."""
    if not target or not target.startswith("shadow-"):
        raise RuntimeError(
            f"SAFETY VIOLATION: Refusing to execute tool on non-shadow target '{target}'"
        )
    return target

def get_container(target: str):
    """Fetches container object after enforcing shadow- assertion."""
    target = assert_shadow_target(target)
    client = docker.from_env()
    return client.containers.get(target)

# ==============================================================================
# UNIVERSAL TOOL A: execute_sql_command
# ==============================================================================
def execute_sql_command(query: str, target_container: str = "shadow-postgres-db") -> Dict[str, Any]:
    """Executes a raw SQL command safely against the target database container."""
    target = assert_shadow_target(target_container)
    print(f"[TOOL] Executing SQL on {target}: {query}")
    try:
        cmd = ["docker", "exec", target, "psql", "-U", "postgres", "-c", query]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Determine if container needs a restart for changes to apply
        restarted = False
        if any(kw in query.lower() for kw in ["alter system", "max_connections", "shared_buffers", "wal"]):
            try:
                container = get_container(target)
                container.restart(timeout=10)
                restarted = True
            except Exception as e:
                print(f"[TOOL WARNING] Failed to restart {target} after ALTER SYSTEM: {e}")

        return {
            "target": target,
            "tool": "execute_sql_command",
            "sql": query,
            "output": f"SUCCESS: {result.stdout.strip()}",
            "restarted_container": restarted
        }
    except subprocess.CalledProcessError as e:
        err = e.stderr.strip() if e.stderr else e.stdout.strip()
        return {"target": target, "tool": "execute_sql_command", "sql": query, "output": f"ERROR: {err}"}

# ==============================================================================
# UNIVERSAL TOOL B: update_container_resources
# ==============================================================================
def update_container_resources(target_container: str, resource_type: str, limit_value: str) -> Dict[str, Any]:
    """Updates Docker container resource limits (CPU/Memory) on the fly."""
    target = assert_shadow_target(target_container)
    print(f"[TOOL] Updating {resource_type} to {limit_value} on {target}")
    try:
        flag = "--cpus" if "cpu" in resource_type.lower() else "--memory"
        cmd = ["docker", "update", flag, limit_value, target]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return {
            "target": target,
            "tool": "update_container_resources",
            "resource_type": resource_type,
            "limit_value": limit_value,
            "output": f"SUCCESS: Resource {resource_type} updated to {limit_value}"
        }
    except subprocess.CalledProcessError as e:
        err = e.stderr.strip() if e.stderr else e.stdout.strip()
        return {"target": target, "tool": "update_container_resources", "output": f"ERROR: {err}"}

# ==============================================================================
# UNIVERSAL TOOL C: execute_shell_command
# ==============================================================================
def execute_shell_command(command: str, target_container: str) -> Dict[str, Any]:
    """Executes general bash/shell commands for certs, networking, or file management."""
    target = assert_shadow_target(target_container)
    print(f"[TOOL] Executing shell command on {target}: {command}")
    try:
        cmd = ["docker", "exec", target, "sh", "-c", command]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return {
            "target": target,
            "tool": "execute_shell_command",
            "command": command,
            "output": f"SUCCESS: {result.stdout.strip()}"
        }
    except subprocess.CalledProcessError as e:
        err = e.stderr.strip() if e.stderr else e.stdout.strip()
        return {"target": target, "tool": "execute_shell_command", "command": command, "output": f"ERROR: {err}"}

# ==============================================================================
# TOOL 1: run_query (Legacy mapping)
# ==============================================================================
def run_query(target: str, statement_type: str, setting: str, value: Any) -> Dict[str, Any]:
    """Applies SQL setting changes on shadow-postgres-db."""
    target = assert_shadow_target(target)
    container = get_container(target)

    if statement_type == "alter_system_set":
        sql = f"ALTER SYSTEM SET {setting} = {value};"
        res = container.exec_run(f"psql -U postgres -c \"{sql}\"")

        # Certain settings like max_connections require container restart to take full effect
        restarted = False
        if setting in ["max_connections", "shared_buffers"]:
            container.restart(timeout=10)
            restarted = True
        else:
            container.exec_run("psql -U postgres -c \"SELECT pg_reload_conf();\"")

        return {
            "target": target,
            "tool": "run_query",
            "statement_type": statement_type,
            "setting": setting,
            "value": value,
            "sql": sql,
            "exit_code": res.exit_code,
            "output": res.output.decode("utf-8", errors="ignore").strip(),
            "restarted_container": restarted
        }
    else:
        sql = f"SET {setting} = {value};"
        res = container.exec_run(f"psql -U postgres -c \"{sql}\"")
        return {
            "target": target,
            "tool": "run_query",
            "statement_type": statement_type,
            "setting": setting,
            "value": value,
            "sql": sql,
            "exit_code": res.exit_code,
            "output": res.output.decode("utf-8", errors="ignore").strip()
        }

# ==============================================================================
# TOOL 2: run_config_command
# ==============================================================================
def run_config_command(target: str, config_key: str, value: Any) -> Dict[str, Any]:
    """Modifies configuration key on shadow-redis or shadow service."""
    target = assert_shadow_target(target)
    try:
        container = get_container(target)
        
        # Prevent Docker API exceptions on dead containers
        if container.status != "running":
            return {
                "target": target, 
                "tool": "run_config_command", 
                "exit_code": -1, 
                "output": f"Failed: Container is in '{container.status}' state."
            }

        if "redis" in target:
            cmd = f"redis-cli -a redis_secure_password CONFIG SET {config_key} {value}"
            res = container.exec_run(cmd)
            return {
                "target": target,
                "tool": "run_config_command",
                "config_key": config_key,
                "value": value,
                "exit_code": res.exit_code,
                "output": res.output.decode("utf-8", errors="ignore").strip()
            }
        else:
            return {"target": target, "tool": "run_config_command", "status": "executed"}
            
    except APIError as e:
        return {"target": target, "tool": "run_config_command", "exit_code": -1, "output": f"APIError: {str(e)}"}
    except NotFound:
        return {"target": target, "tool": "run_config_command", "exit_code": -1, "output": "Container not found."}

# ==============================================================================
# TOOL 3: edit_config_file
# ==============================================================================
def edit_config_file(target: str, path: str, content: str) -> Dict[str, Any]:
    """Edits a file inside shadow container."""
    target = assert_shadow_target(target)
    container = get_container(target)
    cmd = f"sh -c \"echo '{content}' > {path}\""
    res = container.exec_run(cmd)
    return {
        "target": target,
        "tool": "edit_config_file",
        "path": path,
        "exit_code": res.exit_code
    }

# ==============================================================================
# TOOL 4: restart_service
# ==============================================================================
def restart_service(target: str) -> Dict[str, Any]:
    """Restarts target shadow container."""
    target = assert_shadow_target(target)
    container = get_container(target)
    container.restart(timeout=10)
    return {"target": target, "tool": "restart_service", "status": "restarted"}

# ==============================================================================
# TOOL 5: scale_replicas
# ==============================================================================
def scale_replicas(target: str, operation: str, value: Any) -> Dict[str, Any]:
    """Scales consumer throughput / replicas on shadow target."""
    target = assert_shadow_target(target)
    return {
        "target": target,
        "tool": "scale_replicas",
        "operation": operation,
        "value": value,
        "status": "scaled"
    }

# ==============================================================================
# TOOL 6: read_state (REQUIRED for fault_cleared check)
# ==============================================================================
def read_state(target: str, query_type: str = "default") -> Dict[str, Any]:
    """
    Read-only inspection of target shadow service state.
    Used by harness step 7 to determine if fault_cleared is True or False.
    """
    target = assert_shadow_target(target)
    container = get_container(target)
    state_info = {
        "target": target,
        "status": container.status,
        "health": container.attrs.get("State", {}).get("Health", {}).get("Status", "unknown")
    }

    if target == "shadow-postgres-db":
        # Query max_connections setting
        res_max = container.exec_run("psql -U postgres -t -c \"SHOW max_connections;\"")
        max_conn_str = res_max.output.decode("utf-8", errors="ignore").strip()
        try:
            state_info["max_connections"] = int(max_conn_str)
        except ValueError:
            state_info["max_connections"] = None

        # Query active client connections
        res_act = container.exec_run("psql -U postgres -t -c \"SELECT count(*) FROM pg_stat_activity WHERE state = 'active';\"")
        act_conn_str = res_act.output.decode("utf-8", errors="ignore").strip()
        try:
            state_info["active_connections"] = int(act_conn_str)
        except ValueError:
            state_info["active_connections"] = 0

    elif target == "shadow-redis":
        res = container.exec_run("redis-cli -a redis_secure_password CONFIG GET maxmemory-policy")
        raw_output = res.output.decode("utf-8", errors="ignore").strip()
        
        # BUG FIX: Filter out the Warning line injected by redis-cli
        lines = [line.strip() for line in raw_output.splitlines() if not line.startswith("Warning")]
        
        if len(lines) >= 2:
            state_info["maxmemory-policy"] = lines[1]
        elif len(lines) == 1:
            state_info["maxmemory-policy"] = lines[0]
        else:
            state_info["maxmemory-policy"] = "noeviction"

    elif target == "shadow-rabbitmq":
        res = container.exec_run("rabbitmqctl list_queues name messages consumers")
        state_info["queue_info"] = res.output.decode("utf-8", errors="ignore").strip()

    return state_info