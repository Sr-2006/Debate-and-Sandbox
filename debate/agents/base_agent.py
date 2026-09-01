from llm import LLMClient
from prompt_manager import PromptManager
from config import MAX_AGENT_TOKENS

class BaseAgent:
    """Base class for specialized reasoning agents using local LLM inference with async support and in-memory prompt caching."""
    
    def __init__(self, agent_name: str, temperature: float = 0.2, default_tokens: int = MAX_AGENT_TOKENS):
        self.llm = LLMClient()
        self.agent_name = agent_name
        self.temperature = temperature
        self.default_tokens = default_tokens
        self.prompt_manager = PromptManager()

    def load_prompt(self) -> str:
        """Load system prompt from PromptManager in-memory cache."""
        return self.prompt_manager.get_prompt(self.agent_name)

    async def think_detailed_async(self, problem: str, context: str = "", max_tokens: int = None) -> dict:
        """Execute reasoning asynchronously and return detailed output dict."""
        system_prompt = self.load_prompt()
        tokens = max_tokens if max_tokens is not None else self.default_tokens

        if context:
            user_prompt = f"Problem:\n{problem}\n\nDebate Context:\n{context}"
        else:
            user_prompt = f"Problem:\n{problem}"

        return await self.llm.generate_with_meta_async(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=self.temperature,
            max_tokens=tokens
        )
