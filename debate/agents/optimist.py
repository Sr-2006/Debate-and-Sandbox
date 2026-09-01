from agents.base_agent import BaseAgent
from config import OPTIMIST_TEMP, MAX_AGENT_TOKENS

class Optimist(BaseAgent):
    """Recovery Engineer focusing on rapid triage and service restoration."""
    def __init__(self):
        super().__init__("optimist", temperature=OPTIMIST_TEMP, default_tokens=MAX_AGENT_TOKENS)