import json
import re

class IncidentParser:
    """Parses and converts structured JSON incident payloads into token-optimized Markdown for agents."""

    @staticmethod
    def parse_and_format(raw_input: str | dict) -> tuple[str, str]:
        """
        Parses raw input (JSON string, Python dict, or legacy string).
        Returns: (formatted_markdown_prompt, incident_id)
        """
        if isinstance(raw_input, str):
            try:
                data = json.loads(raw_input)
            except (json.JSONDecodeError, TypeError):
                # Fallback for plain text incident input
                match = re.search(r'Incident ID:\s*([A-Za-z0-9_-]+)', raw_input, re.IGNORECASE)
                inc_id = match.group(1) if match else f"incident_raw"
                return raw_input.strip(), inc_id
        elif isinstance(raw_input, dict):
            data = raw_input
        else:
            return str(raw_input), "incident_unknown"

        sys_ctx = data.get("system_context", {})
        event = data.get("incident_event", {})
        topo = data.get("infrastructure_topology", {})
        health = data.get("service_health_status", {})
        telemetry = data.get("telemetry_evidence", {})
        chaos = data.get("injected_chaos_context", {})
        instruction = data.get("agent_instruction", "Analyze the provided telemetry evidence and output a remediation plan.")

        inc_id = event.get("incident_id", "incident")

        formatted = f"""### INCIDENT CONTEXT [{inc_id}]
- **Target Service**: `{event.get('target_service', 'N/A')}` | **Severity**: `{event.get('severity', 'UNKNOWN')}`
- **Role**: {topo.get('role', 'N/A')}
- **Target Status**: `{health.get('docker_status', 'N/A')}` ({health.get('health_check', 'N/A')})
"""
        dep_states = health.get("dependency_states", {})
        if dep_states:
            formatted += "- **Dependencies**:\n"
            for dep, state in dep_states.items():
                formatted += f"  - `{dep}`: {state.get('status', 'unknown')} ({state.get('health', 'unknown')})\n"

        if chaos.get("active_infrastructure_mutations"):
            formatted += f"- ⚠️ **Active Mutation**: {chaos.get('active_infrastructure_mutations')}\n"

        formatted += f"\n### TELEMETRY EVIDENCE\n"
        if telemetry.get("log_cluster_template"):
            formatted += f"- **Log Pattern**: `{telemetry.get('log_cluster_template')}`\n"
        
        # Telemetry Distillation: Limit to top 2 log samples to cut prefill tokens by ~70%
        log_samples = telemetry.get("log_samples", [])[:2]
        if log_samples:
            formatted += "- **Top Log Samples**:\n"
            for log in log_samples:
                formatted += f"  - [{log.get('timestamp')}] {log.get('level')}: {log.get('content')}\n"

        metrics = telemetry.get("metrics_snapshot", [])[:1]
        if metrics:
            formatted += "- **Metrics**: "
            m_parts = []
            for k, v in metrics[0].items():
                if k != "timestamp":
                    m_parts.append(f"{k}={v}")
            formatted += ", ".join(m_parts) + "\n"

        # Phase 2 enrichment: similar incidents + historical resolutions (Fact Checker material).
        similar = data.get("similar_incidents") or []
        if isinstance(similar, list) and similar:
            formatted += "\n### SIMILAR PAST INCIDENTS (Phase 2 vector matches)\n"
            for s in similar[:2]:
                if isinstance(s, dict):
                    sid = s.get("incident_id") or s.get("id") or "unknown"
                    sim = s.get("similarity") or s.get("score")
                    res = s.get("resolution") or s.get("root_cause") or ""
                    formatted += f"- `{sid}`"
                    if sim is not None:
                        formatted += f" (similarity={sim})"
                    if res:
                        formatted += f": {str(res)[:120]}"
                    formatted += "\n"

        historical = data.get("historical_context")
        if historical:
            formatted += f"- **Historical Context**: {str(historical)[:200]}\n"

        formatted += f"\n### TASK INSTRUCTION\n{instruction}"

        return formatted.strip(), inc_id
