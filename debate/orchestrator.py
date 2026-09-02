import json
import re
import asyncio
from llm import LLMClient
from prompt_manager import PromptManager
from json_utils import safe_parse_json
from config import ORCHESTRATOR_TEMP, MAX_ORCHESTRATOR_TOKENS, ORCHESTRATOR_MODEL
from contracts.validation import get_capabilities

class Orchestrator:
    """Lead Incident Commander synthesizes agent debate into a final structured JSON decision with typed capabilities."""
    
    def __init__(self):
        self.llm = LLMClient()
        self.temperature = ORCHESTRATOR_TEMP
        self.prompt_manager = PromptManager()

    def load_prompt(self) -> str:
        """Read orchestrator system prompt from PromptManager in-memory cache."""
        return self.prompt_manager.get_prompt("orchestrator")

    def compress_agent_state(self, responses: dict) -> dict:
        """Compress full agent payloads into a memory-safe state for the orchestrator."""
        compact = {"workers": {}}
        for agent, response in responses.items():
            if not isinstance(response, dict):
                compact["workers"][agent] = {
                    "component": "unknown",
                    "root_cause": str(response),
                    "confidence": 0.0,
                    "evidence": [],
                }
                continue

            compact["workers"][agent] = {
                "component": response.get("primary_component") or response.get("component") or "unknown",
                "root_cause": (response.get("rca") or response.get("triage") or response.get("logic") or ""),
                "confidence": response.get("confidence", response.get("conf", 0.0)),
                "evidence": response.get("evidence") or response.get("evidence_anchors") or [],
                "triage": response.get("triage") or "",
                "safety_flag": bool(response.get("safety_violation")),
            }
        return compact

    def safe_parse_json(self, text: str) -> dict:
        """Robust JSON repair for Orchestrator decision output."""
        return safe_parse_json(text, fallback={
            "problem_summary": text[:200] if text else "Incident reported",
            "root_cause": text[:200] if text else "Unknown cause",
            "primary_component": None,
            "evidence_refs": [],
            "confidence": 0.0,
            "intent": {
                "intent_type": "NO_SUPPORTED_ACTION",
                "mode": "OBSERVE",
                "target_ref": {
                    "kind": "container",
                    "canonical_name": None
                },
                "parameters": {}
            },
            "human_recommendation": "Manual investigation required"
        })

    def normalize_solution(self, parsed: dict) -> dict:
        """Normalizes and validates orchestrator JSON structure."""
        capabilities = get_capabilities()

        # Normalize confidence to float 0.0-1.0
        raw_conf = parsed.get("confidence")
        if raw_conf is not None and isinstance(raw_conf, (int, float)):
            conf_val = float(raw_conf)
            if conf_val > 1.0:
                conf_val = conf_val / 100.0
            parsed["confidence"] = round(min(1.0, max(0.0, conf_val)), 2)
        else:
            parsed["confidence"] = None

        # Ensure evidence_refs is a list
        ev_refs = parsed.get("evidence_refs", [])
        if isinstance(ev_refs, str):
            ev_refs = [ev_refs] if ev_refs.strip() else []
        elif not isinstance(ev_refs, list):
            ev_refs = []
        parsed["evidence_refs"] = ev_refs

        # Normalize intent structure
        intent = parsed.get("intent")
        if not isinstance(intent, dict):
            intents_arr = parsed.get("intents", [])
            if isinstance(intents_arr, list) and intents_arr and isinstance(intents_arr[0], dict):
                intent = intents_arr[0]
            else:
                intent = {}

        intent_type = intent.get("intent_type", "NO_SUPPORTED_ACTION")
        target_ref = intent.get("target_ref", {})
        if not isinstance(target_ref, dict):
            target_ref = {}

        primary_component = parsed.get("primary_component") or target_ref.get("canonical_name")
        target_kind = target_ref.get("kind") or "container"


        # Check if intent_type is in registered capabilities catalog
        if intent_type not in capabilities and intent_type != "NO_SUPPORTED_ACTION":
            # Will be handled by repair prompt caller if unmapped
            pass

        parsed["primary_component"] = primary_component
        parsed["intent"] = {
            "intent_type": intent_type,
            "mode": intent.get("mode", capabilities.get(intent_type, {}).get("mode", "OBSERVE")),
            "target_ref": {
                "kind": target_kind,
                "canonical_name": primary_component
            },
            "parameters": intent.get("parameters", {}) if isinstance(intent.get("parameters"), dict) else {}
        }
        return parsed

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

        parsed_solution = self.normalize_solution(self.safe_parse_json(meta["response"]))
        capabilities = get_capabilities()
        intent_type = parsed_solution["intent"]["intent_type"]

        # If capability is unmapped and not NO_SUPPORTED_ACTION, issue 1 repair prompt
        if intent_type not in capabilities and intent_type != "NO_SUPPORTED_ACTION":
            allowed_caps = list(capabilities.keys())
            repair_prompt = (
                f"The proposed capability '{intent_type}' is not a registered catalog capability.\n"
                f"Allowed capability names are:\n" + "\n".join([f"- {c}" for c in allowed_caps]) + "\n\n"
                f"Instructions:\n"
                f"1. Choose a compatible capability from the allowed list with valid parameters.\n"
                f"2. Or if no capability matches, set \"intent_type\": \"NO_SUPPORTED_ACTION\".\n"
                f"3. Return ONLY valid JSON matching the exact schema.\n\n"
                f"Previous JSON output:\n{json.dumps(parsed_solution, ensure_ascii=False)}"
            )

            repair_meta = await self.llm.generate_with_meta_async(
                system_prompt=system_prompt,
                user_prompt=repair_prompt,
                temperature=0.1,
                max_tokens=MAX_ORCHESTRATOR_TOKENS,
                model=ORCHESTRATOR_MODEL
            )
            repaired = self.normalize_solution(self.safe_parse_json(repair_meta["response"]))
            repaired_intent_type = repaired["intent"]["intent_type"]

            if repaired_intent_type in capabilities:
                parsed_solution = repaired
            else:
                # Still unmapped -> set NO_SUPPORTED_ACTION without guessing
                parsed_solution["intent"]["intent_type"] = "NO_SUPPORTED_ACTION"

        conf_score = parsed_solution.get("confidence", 0.75)
        return {
            "solution": parsed_solution,
            "confidence_score": conf_score,
            "prompt": meta["prompt"],
            "latency": meta["latency"]
        }
