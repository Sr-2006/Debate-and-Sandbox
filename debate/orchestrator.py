import json
import re
import asyncio
from llm import LLMClient
from prompt_manager import PromptManager
from json_utils import safe_parse_json
from config import ORCHESTRATOR_TEMP, MAX_ORCHESTRATOR_TOKENS, ORCHESTRATOR_MODEL

class Orchestrator:
    """Lead Incident Commander synthesizes agent debate into a final JSON decision with primary_component, action commands & dynamic confidence scoring."""
    
    def __init__(self):
        self.llm = LLMClient()
        self.temperature = ORCHESTRATOR_TEMP
        self.prompt_manager = PromptManager()

    def load_prompt(self) -> str:
        """Read orchestrator system prompt from PromptManager in-memory cache."""
        return self.prompt_manager.get_prompt("orchestrator")

    def compress_agent_state(self, responses: dict) -> dict:
        """Compress full agent payloads into a memory-safe state for the orchestrator.

        This preserves the signal needed for final synthesis while discarding noisy
        raw chatter, which is essential on laptops with limited VRAM and tight prompt
        budgets.
        """
        compact = {"workers": {}}
        for agent, response in responses.items():
            if not isinstance(response, dict):
                compact["workers"][agent] = {
                    "component": "unknown",
                    "root_cause": str(response),
                    "confidence": 0,
                    "evidence": [],
                }
                continue

            compact["workers"][agent] = {
                "component": response.get("primary_component") or response.get("component") or "unknown",
                "root_cause": (response.get("rca") or response.get("triage") or response.get("logic") or ""),
                "confidence": response.get("confidence", response.get("conf", 0)),
                "evidence": response.get("evidence") or response.get("evidence_anchors") or [],
                "triage": response.get("triage") or "",
                "safety_flag": bool(response.get("safety_violation")),
            }
        return compact

    def safe_parse_json(self, text: str) -> dict:
        """Robust JSON repair for Orchestrator decision output (shared util)."""
        return safe_parse_json(text, fallback={
            "consensus_rc": text,
            "primary_component": "Unknown",
            "consensus_quality": "LOW",
            "final_triage": text,
            "final_stab": "",
            "final_rca": text,
            "action_commands": [],
            "confidence": 60,
            "reasoning": "Parse fallback"
        })

    async def synthesize_detailed_async(self, problem_header: str, responses: dict) -> dict:
        """Synthesize agent responses asynchronously using lean context payload in Native JSON mode."""
        system_prompt = self.load_prompt()
        compact_state = self.compress_agent_state(responses)

        user_prompt = (
            f"Incident Summary:\n{problem_header}\n\n"
            f"Compressed Agent State:\n{json.dumps(compact_state, ensure_ascii=False)}\n"
        )

        meta = await self.llm.generate_with_meta_async(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=self.temperature,
            max_tokens=MAX_ORCHESTRATOR_TOKENS,
            model=ORCHESTRATOR_MODEL
        )

        parsed_solution = self.safe_parse_json(meta["response"])
        confidence = parsed_solution.get("confidence", 75)
        if isinstance(confidence, str):
            match = re.search(r'\d+', confidence)
            confidence = int(match.group(0)) if match else 75

        return {
            "solution": parsed_solution,
            "confidence_score": confidence,
            "prompt": meta["prompt"],
            "latency": meta["latency"]
        }
