# Final_v1: 6GB-VRAM laptop build

This branch is the hardened, memory-safe version of the debate engine intended for a laptop-class deployment with a 6GB VRAM ceiling.

## Model strategy

- Worker agents: qwen2.5:3b
- Orchestrator: qwen2.5:7b
- Runtime rule: only one worker batch at a time and a single orchestrator synthesis pass
- No full parallel multi-model inference under the laptop budget

## Architectural changes

1. Memory-safe runtime budget
   - lower token caps
   - lower context window
   - reduced parallelism
   - focused structured state passing

2. Orchestrator compression
   - the orchestrator no longer consumes raw agent chatter directly
   - it receives compact structured state only
   - this preserves quality while reducing memory pressure and prompt bloat

3. Evidence gate
   - weak or generic telemetry is capped before confidence can become autonomous
   - if evidence is shallow, the system routes to sandbox instead of confident execution

4. Risk-aware action policy
   - high-risk commands with weak evidence are forced into review/sandbox mode
   - destructive or mutation-like instructions are handled as operational risk, not just string matches

5. Safety-first behavior
   - if the evidence is not grounded, the system refuses high-confidence output
   - if risk is high, the system does not allow autonomous execution

## Runtime rule set

- Autonomous only when confidence is high and evidence is grounded
- Sandbox when evidence is weak or the action is high-risk
- Reject or re-run when command-level risk is severe and no evidence supports it

## Validation

Verified with:

- python -m pytest -q tests/test_scoring.py tests/test_llm.py

Current result:

- 22 passed in 28.79s

## Notes

This branch is intentionally conservative. The system is designed to be reliable on a laptop with limited VRAM rather than flashy under ideal conditions.
