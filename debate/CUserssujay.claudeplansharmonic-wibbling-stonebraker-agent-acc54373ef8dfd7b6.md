# Implementation Plan: Multi-Agent RCA Engine 'God Tier' Upgrade

## Overview
Upgrade the RCA engine from basic keyword-based consensus to a high-fidelity semantic engine with safety linting, latency penalties, and optimized multi-round debate logic.

## 1. Requirements & Dependency Updates
### `requirements.txt`
- Add `sentence-transformers` for semantic similarity.
- Add `torch` (as dependency for sentence-transformers).

### `config.py`
Add the following constants:
- `VETO_COMMAND_REGEX`: Regex for destructive commands (e.g., `rm -rf`, `delete namespace`, `drop table`, `mkfs`).
- `MIN_CONFIDENCE_THRESHOLD = 85`
- `MAX_LATENCY_THRESHOLD = 20.0`
- `LATENCY_PENALTY_PER_SEC = 0.5`
- `SANDBOX_LOWER_BOUND = 65`
- `SANDBOX_UPPER_BOUND = 84`
- `EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"`

## 2. Core Component Modifications

### `scoring.py` (Semantic & Strict Scoring)
- **Semantic Consensus**: 
    - Initialize `SentenceTransformer(EMBEDDING_MODEL_NAME)`.
    - Replace Jaccard overlap in `calculate_deterministic_confidence` with Cosine Similarity of agent RCA embeddings.
- **Command Veto Linter**: 
    - Implement a check against `VETO_COMMAND_REGEX` on `orchestrator_solution["action_commands"]`.
    - If violation: set actionability score to 0 and cap final confidence at 64%.
- **Confidence Strictness (Genius Improvisations)**:
    - **Cross-Agent Divergence Penalty**: Extract "Primary Component" from agent responses. If agents disagree on the core component, apply a -10 point penalty.
    - **Evidence-to-Action Mapping**: Verify that every `action_command` refers to a component or metric cited in the `reasoning`.
    - **Schema-Strictness Bonus**: Add +5 points for perfect JSON adherence and concise Micro-CoT.
- **Return Signature**: Update `calculate_deterministic_confidence` to return a dict: `{ "score": int, "safety_violation": bool, "divergence_penalty": float, "schema_bonus": int }`.

### `debate_manager.py` (Pipeline & Round 2 Optimization)
- **MTTR Decay Penalty**: 
    - Calculate `total_latency = time.perf_counter() - pipeline_start`.
    - If `total_latency > MAX_LATENCY_THRESHOLD`, subtract `(total_latency - MAX_LATENCY_THRESHOLD) * LATENCY_PENALTY_PER_SEC` from final confidence.
- **Round 2 Optimization**:
    - **Selective Re-sampling**: If $\ge 2$ agents were in semantic consensus in R1, only re-run the outlier agent and the Orchestrator in R2.
    - **Conflict Summary Distillation**: Instead of the full problem, pass a distilled "Conflict Summary" (where agents disagreed) to the R2 agents.
    - **Internal Debate**: Pass the full reasoning of other agents from R1 into the R2 prompts.
    - **Guided Revision Constraints**: Replace "NEGATIVE CONSTRAINT" with a "Revision Constraint" containing:
        - The failed hypothesis.
        - Failure reason (e.g., "Confidence $X\%$ below threshold").
        - Explicit pivot instructions.
- **Threshold Barriers**:
    - **Shadow Sandbox**: If final confidence $\in [65, 84]$ OR `safety_violation == True`, mark result as `move_to_sandbox: true`.
    - **Re-iteration Logic**: Trigger R2 if `calc_confidence < 85` OR Orchestrator marks as "Low Consensus".

### `orchestrator.py` (Synthesis Update)
- **Prompt Update**: Modify the system prompt to require the Orchestrator to:
    - Identify a "Primary Component" for each agent's hypothesis.
    - Explicitly mark consensus as "Low" if evidence is contradictory.
- **JSON Schema**: Ensure the output includes `primary_component` and `consensus_quality`.

### `llm.py`
- No major logic changes, but ensure `num_ctx` is sufficient for "Internal Debate" contexts in Round 2.

## 3. 'God Tier' Decision Tree Logic Flow

1. **Round 1 (Parallel Execution)** $\rightarrow$ $\text{Agents} \rightarrow \text{Orchestrator} \rightarrow \text{Semantic Scoring}$.
2. **Safety Check (Veto Linter)** $\rightarrow$ If Destructive Command $\rightarrow$ $\text{Conf} = \min(\text{Conf}, 64\%)$ and $\text{SafetyViolation} = \text{True}$.
3. **Termination Branch**:
    - $\text{Conf} \ge 85\% \text{ AND } \text{SafetyViolation} = \text{False} \implies \text{TERMINATE (Success)}$.
    - $\text{Conf} \in [65, 84] \text{ OR } \text{SafetyViolation} = \text{True} \implies \text{TERMINATE } \rightarrow \text{SHADOW SANDBOX}$.
    - $\text{Conf} < 65\% \text{ OR } \text{Orchestrator} = \text{"Low Consensus"} \implies \text{TRIGGER ROUND 2}$.
4. **Round 2 (Optimized Pivot)**:
    - $\text{Conflict Summary} + \text{Internal Debate} + \text{Guided Revision} \rightarrow \text{Selective Re-sampling}$.
5. **Final Scoring**: Repeat scoring $\rightarrow$ Apply **MTTR Decay Penalty** (latency $> 20\text{s}$).
6. **Final Outcome**: Final Decision based on resulting confidence.

## 4. Verification Plan

### Test Case 1: Semantic Consensus
- Input: Two agents describing the same root cause using different terminology (e.g., "Disk I/O saturation" vs "Storage throughput bottleneck").
- Expected: High semantic similarity score (Cosine) despite low keyword overlap.

### Test Case 2: Veto Linter
- Input: Orchestrator suggests `rm -rf /var/log/app` as a fix.
- Expected: Confidence capped at 64%, `safety_violation: true`, and `move_to_sandbox: true`.

### Test Case 3: MTTR Decay
- Input: Force a delay in `llm.py` to make total pipeline time 30s.
- Expected: Confidence score reduced by $(30 - 20) \times 0.5 = 5$ points.

### Test Case 4: Round 2 Optimization
- Input: Incident with contradictory telemetry causing R1 divergence.
- Expected: Trigger R2, verify only outlier agent is called (if applicable), and verify "Conflict Summary" is in the prompt.

### Test Case 5: Divergence Penalty
- Input: Agent A says "Network Latency", Agent B says "Database Lock".
- Expected: -10 point divergence penalty applied to confidence.

## 5. Critical Files for Implementation
- `scoring.py`
- `debate_manager.py`
- `orchestrator.py`
- `config.py`
- `requirements.txt`
EOF_PLAN`
