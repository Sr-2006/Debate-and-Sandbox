import asyncio
from config import WORKER_MODEL, ORCHESTRATOR_MODEL
from llm import LLMClient
from orchestrator import Orchestrator

def test_default_model_split_uses_3b_workers_and_7b_orchestrator():
    assert WORKER_MODEL == "qwen2.5:3b"
    assert ORCHESTRATOR_MODEL == "qwen2.5:7b"


def test_orchestrator_compacts_agent_state_for_memory_safe_synthesis():
    orchestrator = Orchestrator()
    compact = orchestrator.compress_agent_state({
        "optimist": {
            "primary_component": "network",
            "rca": "DNS timeouts to upstream service",
            "confidence": 82,
            "evidence": ["dns timeout", "coredns degraded"],
            "triage": "restart coredns"
        },
        "critic": {
            "primary_component": "network",
            "rca": "dependency failure on coredns",
            "confidence": 77,
            "evidence": ["coredns degraded"]
        }
    })

    assert "workers" in compact
    assert compact["workers"]["optimist"]["component"] == "network"
    assert "root_cause" in compact["workers"]["optimist"]
    assert "evidence" in compact["workers"]["optimist"]


def test_llm_generate():
    llm = LLMClient()

    meta = asyncio.run(llm.generate_with_meta_async(
        system_prompt="You are a helpful assistant.",
        user_prompt="what is the capital of France?"
    ))

    assert meta["response"] is not None
    print(meta["response"])