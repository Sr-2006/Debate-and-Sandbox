import json
import os
import re
from datetime import datetime
from config import BASE_DIR
from incident_parser import IncidentParser

class DebateLogger:
    """Structured recorder for multi-agent debate executions with Python-native Markdown rendering."""

    def __init__(self, output_dir: str = os.path.join(BASE_DIR, "output")):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.data = {
            "incident_id": None,
            "timestamp": datetime.now().isoformat(),
            "problem": "",
            "round_1": {},
            "consensus": {
                "score": 1.0,
                "threshold": 0.85,
                "debate_required": False
            },
            "round_2": None,
            "orchestrator": {},
            "performance": {
                "round1_time": 0.0,
                "round2_time": 0.0,
                "orchestrator_time": 0.0,
                "total_pipeline_time": 0.0
            }
        }

    def log_problem(self, problem: str | dict, incident_id: str = None):
        """Record problem statement and identify/set incident_id using IncidentParser."""
        formatted_problem, extracted_id = IncidentParser.parse_and_format(problem)
        self.data["problem"] = formatted_problem
        self.data["incident_id"] = incident_id if incident_id else extracted_id

    def log_round1(self, agent_name: str, prompt: str, response: str | dict, latency: float):
        """Record a single agent's Round 1 prompt, response, and latency."""
        self.data["round_1"][agent_name] = {
            "prompt": prompt,
            "response": response,
            "latency": round(latency, 2)
        }

    def log_consensus(self, score: float, threshold: float, debate_required: bool):
        """Record consensus evaluation metrics."""
        self.data["consensus"] = {
            "score": round(score, 3),
            "threshold": threshold,
            "debate_required": debate_required
        }

    def log_orchestrator(self, prompt: str, technical_solution: str | dict, confidence: str | int, latency: float):
        """Record orchestrator synthesis prompt, final technical solution, confidence, and latency."""
        conf_str = f"{confidence}%" if isinstance(confidence, int) else str(confidence)
        self.data["orchestrator"] = {
            "prompt": prompt,
            "technical_solution": technical_solution,
            "confidence": conf_str,
            "latency": round(latency, 2)
        }

    def log_latency(self, round1_time: float, round2_time: float, orchestrator_time: float, total_pipeline_time: float):
        """Record execution time for each pipeline stage and total runtime."""
        self.data["performance"] = {
            "round1_time": round(round1_time, 2),
            "round2_time": round(round2_time, 2),
            "orchestrator_time": round(orchestrator_time, 2),
            "total_pipeline_time": round(total_pipeline_time, 2)
        }

    def render_markdown_report(self) -> str:
        """Render human-readable Markdown summary report offloaded to Python f-strings (God Tier Upgrade)."""
        d = self.data
        inc_id = d.get("incident_id", "INCIDENT")
        ts = d.get("timestamp", "")
        perf = d.get("performance", {})
        cons = d.get("consensus", {})

        orch = d.get("orchestrator", {})
        sol = orch.get("technical_solution", {}) if isinstance(orch, dict) else {}

        md = []
        md.append(f"# Multi-Agent Debate Execution Report: {inc_id}")
        md.append(f"**Timestamp:** `{ts}`  ")
        md.append(f"**Total Pipeline Latency:** `{perf.get('total_pipeline_time', 0.0)}s`  ")
        md.append(f"**Consensus Score:** `{cons.get('score', 0.0)}` (Threshold: `{cons.get('threshold', 0.85)}`)  ")
        md.append(f"**Round 2 Debated:** `{'Yes' if cons.get('debate_required') else 'No (Single Pass Optimization)'}`  ")
        md.append(f"**Calculated Confidence Score:** `{orch.get('confidence', 'N/A')}`\n")
        
        if isinstance(sol, dict) and sol.get("safety_violation"):
            md.append("⚠️ **COMMAND VETO LINTER WARNING**: Destructive command detected in remediation plan. Execution capped at max 64% and routed to Shadow Sandbox.\n")

        if isinstance(sol, dict) and sol.get("telemetry_hazard_detected"):
            md.append("⚠️ **TELEMETRY HAZARD DETECTED**: Destructive command/lure present in raw telemetry input payload (flagged as metadata).\n")

        md.append("---")

        md.append("## 1. Problem Statement")
        md.append(f"```text\n{d.get('problem', '')}\n```\n")

        md.append("## 2. Performance & Timing Benchmarks")
        md.append("| Pipeline Phase | Duration (seconds) |")
        md.append("| :--- | :--- |")
        md.append(f"| Round 1 Analysis | {perf.get('round1_time', 0.0)}s |")
        md.append(f"| Round 2 Iterative Debate | {perf.get('round2_time', 0.0)}s |")
        md.append(f"| Orchestrator Synthesis | {perf.get('orchestrator_time', 0.0)}s |")
        md.append(f"| **Total Execution Latency** | **{perf.get('total_pipeline_time', 0.0)}s** |\n")

        role_titles = {
            "optimist": "RECOVERY ENGINEER (Optimist)",
            "critic": "RELIABILITY ENGINEER (Critic)",
            "fact_checker": "VERIFICATION ENGINEER (Fact Checker)"
        }

        md.append("## 3. Round 1: Independent Agent Analysis")
        for agent_name, info in d.get("round_1", {}).items():
            title = role_titles.get(agent_name, agent_name.upper())
            md.append(f"### {title} (Latency: {info.get('latency', 0.0)}s)")
            resp = info.get("response", {})
            if isinstance(resp, dict):
                if "logic" in resp:
                    md.append(f"**Micro-CoT Logic**: *{resp.get('logic')}*\n")
                md.append("#### 1. TRIAGE (0-5 minutes)")
                md.append(f"{resp.get('triage', '')}\n")
                md.append("#### 2. STABILIZATION (5-60 minutes)")
                md.append(f"{resp.get('stab', '')}\n")
                md.append("#### 3. ROOT CAUSE ANALYSIS")
                md.append(f"{resp.get('rca', '')}\n")
            else:
                md.append(f"{resp}\n")

        md.append("## 4. Orchestrator Synthesis & Final Recovery Plan")
        md.append(f"**Synthesis Latency:** `{orch.get('latency', 0.0)}s` | **Confidence Score:** `{orch.get('confidence', 'N/A')}`\n")
        
        if isinstance(sol, dict):
            if sol.get("primary_component"):
                md.append(f"**Primary Component**: `{sol.get('primary_component')}` | **Consensus Quality**: `{sol.get('consensus_quality', 'HIGH')}`\n")
            md.append(f"### 1. Executive Summary & Root Cause\n{sol.get('consensus_rc', '')}\n")
            md.append(f"### 2. Final Technical Recovery Solution\n")
            md.append(f"#### TRIAGE (0-5 minutes)\n{sol.get('final_triage', '')}\n")
            md.append(f"#### STABILIZATION (5-60 minutes)\n{sol.get('final_stab', '')}\n")
            md.append(f"#### ROOT CAUSE ANALYSIS\n{sol.get('final_rca', '')}\n")
            
            cmds = sol.get("action_commands", [])
            if cmds:
                md.append("#### EXECUTABLE REMEDIATION COMMANDS")
                md.append("```bash")
                for cmd in cmds:
                    md.append(f"{cmd}")
                md.append("```\n")
            
            md.append(f"### 3. Confidence Reasoning\n{sol.get('reasoning', '')}\n")
        else:
            md.append(f"{sol}\n")

        return "\n".join(md)

    def save(self) -> tuple[str, str]:
        """Write execution logs as JSON and Markdown files inside output/ directory."""
        inc_id = self.data.get("incident_id") or "incident"
        
        json_path = os.path.join(self.output_dir, f"{inc_id}.json")
        md_path = os.path.join(self.output_dir, f"{inc_id}.md")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4)

        md_content = self.render_markdown_report()
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        print(f"\n[DebateLogger] Debate execution recorded successfully:")
        print(f"  - JSON: {os.path.abspath(json_path)}")
        print(f"  - MD:   {os.path.abspath(md_path)}")

        return json_path, md_path
