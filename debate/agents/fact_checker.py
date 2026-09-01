from agents.base_agent import BaseAgent
from config import FACT_CHECKER_TEMP, MAX_AGENT_TOKENS

class FactChecker(BaseAgent):
    """Verification Engineer focusing on evidence validation and log verification."""
    def __init__(self):
        super().__init__("fact_checker", temperature=FACT_CHECKER_TEMP, default_tokens=MAX_AGENT_TOKENS)