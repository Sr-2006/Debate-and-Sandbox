import re
import os
import json
import time
import hashlib
import httpx
import asyncio
from pathlib import Path
from config import MODEL_NAME, OLLAMA_API_URL, MAX_AGENT_TOKENS, NUM_CTX, REQUEST_TIMEOUT

# Deterministic test mode: when DEBATE_MOCK_LLM=1, return canned responses from
# tests/fixtures/ keyed by a hash of the prompt. Makes scores reproducible in CI.
MOCK_MODE = os.environ.get("DEBATE_MOCK_LLM") == "1"
MOCK_FIXTURES_DIR = Path(__file__).resolve().parent / "tests" / "fixtures"


class LLMClient:
    """Handles async communication with local Ollama model enforcing native JSON mode and strict num_ctx context capping."""

    def _mock_response(self, system_prompt: str, user_prompt: str) -> str | None:
        """Return a canned fixture response for this prompt, if one exists."""
        key = hashlib.sha256(f"{system_prompt}\n{user_prompt}".encode()).hexdigest()[:16]
        fixture = MOCK_FIXTURES_DIR / f"{key}.json"
        if fixture.exists():
            try:
                return json.loads(fixture.read_text(encoding="utf-8"))["response"]
            except Exception:
                return None
        return None

    async def generate_with_meta_async(self, system_prompt: str, user_prompt: str, temperature: float = 0.2, max_tokens: int = MAX_AGENT_TOKENS, model: str = None) -> dict:
        """Generate response asynchronously in Native JSON mode with surgical num_ctx memory cap."""
        start_time = time.perf_counter()

        full_prompt = f"[SYSTEM PROMPT]:\n{system_prompt}\n\n[USER PROMPT]:\n{user_prompt}"

        # Deterministic mock mode for reproducible tests (no Ollama needed).
        if MOCK_MODE:
            canned = self._mock_response(system_prompt, user_prompt)
            if canned is not None:
                return {
                    "response": canned,
                    "prompt": full_prompt,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "latency": 0.0
                }

        payload = {
            "model": model or MODEL_NAME,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "format": "json",
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": NUM_CTX
            },
            "stream": False
        }

        # Retry with exponential backoff on transient network/timeout failures.
        res_data = None
        last_err = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    response = await client.post(OLLAMA_API_URL, json=payload)
                    response.raise_for_status()
                    res_data = response.json()
                break
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_err = e
                break
            except Exception as e:
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)

        if res_data is None:
            print(f"[LLM ERROR] Async HTTP connection failed after retries: {last_err}")
            return {
                "response": f"{{\"error\": \"Unable to generate response due to {last_err}\"}}",
                "prompt": full_prompt,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "latency": 0.0
            }
        
        end_time = time.perf_counter()
        latency = end_time - start_time

        content = res_data.get("message", {}).get("content", "").strip()

        print(f"[LLM ({payload['model']}) | Temp: {temperature} | Latency: {latency:.2f}s]")

        return {
            "response": content,
            "prompt": full_prompt,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "latency": round(latency, 2)
        }
