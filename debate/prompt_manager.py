from pathlib import Path
from config import OPTIMIST_PROMPT, CRITIC_PROMPT, FACT_CHECKER_PROMPT, ORCHESTRATOR_PROMPT

class PromptManager:
    """Singleton Prompt Manager that loads all prompt templates into memory at startup."""
    _instance = None
    _prompts = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PromptManager, cls).__new__(cls)
            cls._instance._load_all_prompts()
        return cls._instance

    def _load_all_prompts(self):
        """Pre-load all prompt text files into memory cache."""
        self._prompts = {
            "optimist": Path(OPTIMIST_PROMPT).read_text(encoding="utf-8").strip(),
            "critic": Path(CRITIC_PROMPT).read_text(encoding="utf-8").strip(),
            "fact_checker": Path(FACT_CHECKER_PROMPT).read_text(encoding="utf-8").strip(),
            "orchestrator": Path(ORCHESTRATOR_PROMPT).read_text(encoding="utf-8").strip()
        }

    def get_prompt(self, agent_name: str) -> str:
        """Retrieve cached prompt string by agent key."""
        return self._prompts.get(agent_name.lower(), "")
