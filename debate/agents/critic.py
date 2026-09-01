from agents.base_agent import BaseAgent
from config import CRITIC_TEMP, MAX_AGENT_TOKENS

class Critic(BaseAgent):
    """Reliability Engineer focusing on risk control and safety validation."""
    def __init__(self):
        super().__init__("critic", temperature=CRITIC_TEMP, default_tokens=MAX_AGENT_TOKENS)