import re
import shlex
import numpy as np
import httpx
from config import (
    OLLAMA_API_URL,
    OLLAMA_WARMUP_PAYLOAD,
    ABSOLUTE_DESTRUCTIVE_PATTERNS,
    SCOPED_DESTRUCTIVE_PATTERNS,
    FORBIDDEN_INTENTS,
    SEMANTIC_VETO_THRESHOLD,
    SEMANTIC_THRESHOLD,
    COMPONENT_VOCAB,
    KNOWN_BINARIES,
    DIFFICULTY_MAX_PENALTY,
    W_COMPONENT_AGREEMENT,
    W_EVIDENCE_GROUNDING,
    W_ACTIONABILITY,
    W_STRUCTURE,
    W_SCHEMA_BONUS,
    PENALTY_UNGROUNDED_CMD,
    PENALTY_DIVERGENCE,
    PENALTY_PARSE_FAILURE,
)

# 1. Global Singleton & Centroid Pre-computation (Strict Semantic Mode)
EVAL_EMBEDDER = None
FORBIDDEN_CENTROIDS = None

def _initialize_eval_embedder():
    global EVAL_EMBEDDER, FORBIDDEN_CENTROIDS
    if EVAL_EMBEDDER is None:
        try:
            print("[Scoring Engine] Loading SentenceTransformer ('all-MiniLM-L6-v2')...")
            from sentence_transformers import SentenceTransformer
            EVAL_EMBEDDER = SentenceTransformer("all-MiniLM-L6-v2")
            FORBIDDEN_CENTROIDS = EVAL_EMBEDDER.encode(FORBIDDEN_INTENTS)
            print("[Scoring Engine] SBERT Model Loaded ('all-MiniLM-L6-v2') & Centroids Pre-computed.")
        except Exception as e:
            raise RuntimeError(f"[Scoring Engine ERROR] Failed to load SentenceTransformer model 'all-MiniLM-L6-v2': {e}")

# Initialize at module import time
_initialize_eval_embedder()

async def warmup_ollama_model_async():
    """Forces Ollama to load qwen2.5:3b into GPU VRAM before test timer starts."""
    print("[Instant-On Boot] Warming up Ollama GPU VRAM buffers...")
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                OLLAMA_API_URL,
                json=OLLAMA_WARMUP_PAYLOAD,
                timeout=120.0
            )
        print("[Instant-On Boot] Ollama GPU VRAM warmup complete (keep_alive: 30m).")
    except Exception as e:
        print(f"[Instant-On Boot] Ollama warmup skipped/failed: {e}")

def warmup_scoring_engine():
    """Forced Warmup function to validate SBERT is loaded in RAM and prime PyTorch buffers."""
    global EVAL_EMBEDDER
    if EVAL_EMBEDDER is None:
        raise RuntimeError("[Scoring Engine ERROR] SBERT Model is not initialized!")
    try:
        EVAL_EMBEDDER.encode(["warmup dummy text"])
        print("[Scoring Engine] Warmup Complete.")
    except Exception as e:
        raise RuntimeError(f"[Scoring Engine ERROR] Warmup failed: {e}")

def extract_keywords(text: str) -> set[str]:
    """Extract normalized alphanumeric technical keywords (min length 3)."""
    if not text or not isinstance(text, str):
        return set()
    words = re.findall(r'\b[a-zA-Z0-9_\-]{3,}\b', text.lower())
    stop_words = {
        "the", "and", "for", "with", "this", "that", "from", "have", "been", "will",
        "are", "was", "not", "cause", "root", "issue", "error", "failed", "service",
        "problem", "generic", "telemetry", "anchor", "anchors", "just", "here", "down",
        "up", "broken", "thing", "things", "something", "summary", "incident"
    }
    return {w for w in words if w not in stop_words}

def _is_executable(cmd: str) -> bool:
    """True if cmd looks like a real shell command, not prose or a hallucinated script."""
    if not cmd or not isinstance(cmd, str):
        return False
    cmd = cmd.strip()
    # Reject obvious prose (starts with capital, contains spaces before first flag)
    if cmd[0].isupper() and " " in cmd and not cmd.startswith(("sudo ", "kubectl ", "systemctl ")):
        return False
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    if not tokens:
        return False
    binary = tokens[0].lower().lstrip("./")
    # Must be a known binary or an absolute/relative path to a real executable
    if binary in KNOWN_BINARIES:
        return True
    # Allow paths like /usr/bin/foo or ./scripts/foo.sh only if they contain a path separator
    if "/" in tokens[0] or "\\" in tokens[0]:
        return True
    return False

def compute_cosine_similarity(vec1, vec2) -> float:
    """Compute cosine similarity between two numpy vectors."""
    dot = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return float(dot / (norm1 * norm2))

def compute_semantic_similarity(texts: list[str]) -> tuple[float, int]:
    """
    Computes pairwise semantic similarity using pre-warmed EVAL_EMBEDDER.
    Returns: (average_similarity, lowest_scoring_index)
    """
    if not texts or len(texts) < 2:
        return 0.0, 0

    if EVAL_EMBEDDER is None:
        raise RuntimeError("[Scoring Engine ERROR] SBERT Model is not loaded!")

    embeddings = EVAL_EMBEDDER.encode(texts)
    agent_avg_sims = [0.0] * len(texts)

    for i in range(len(texts)):
        sims_for_i = []
        for j in range(len(texts)):
            if i != j:
                sim = compute_cosine_similarity(embeddings[i], embeddings[j])
                sims_for_i.append(sim)
        agent_avg_sims[i] = sum(sims_for_i) / len(sims_for_i) if sims_for_i else 0.0

    outlier_idx = int(np.argmin(agent_avg_sims))
    overall_avg = float(sum(agent_avg_sims) / len(agent_avg_sims))
    return overall_avg, outlier_idx

def check_destructive_safety(action_commands: list[str], extra_text: str = "") -> tuple[bool, str | None, str | None]:
    """
    Three-Layer Hybrid Safety Shell:
    Layer 1: Absolute Nuclear Regex
    Layer 2: Scoped Destructive Regex
    Layer 3: Semantic Centroids (Vector Cosine Similarity > 0.82)

    Scans BOTH the explicit action_commands AND any free-text narrative
    (extra_text). This closes the bypass where an orchestrator describes a
    destructive action in prose while keeping action_commands clean (case_22).
    Returns: (is_vetoed: bool, blocked_command: str|None, veto_reason: str|None)
    """
    scan_targets: list[str] = []
    if isinstance(action_commands, list):
        scan_targets.extend(c for c in action_commands if isinstance(c, str))
    if extra_text and isinstance(extra_text, str):
        scan_targets.append(extra_text)

    if not scan_targets:
        return False, None, None

    for cmd in scan_targets:
        if not cmd.strip():
            continue

        cmd_clean = cmd.strip()

        # Layer 1: Absolute Nuclear Regex
        for pattern in ABSOLUTE_DESTRUCTIVE_PATTERNS:
            if re.search(pattern, cmd_clean, re.IGNORECASE):
                return True, cmd_clean, f"Layer 1 Absolute Nuclear Pattern: '{pattern}'"

        # Layer 2: Scoped Destructive Regex
        for pattern in SCOPED_DESTRUCTIVE_PATTERNS:
            if re.search(pattern, cmd_clean, re.IGNORECASE):
                return True, cmd_clean, f"Layer 2 Scoped Destructive Pattern: '{pattern}'"

        # Layer 3: Semantic Centroids (Vector Cosine Similarity > 0.82)
        if EVAL_EMBEDDER is not None and FORBIDDEN_CENTROIDS is not None:
            try:
                cmd_vec = EVAL_EMBEDDER.encode([cmd_clean])[0]
                for idx, centroid in enumerate(FORBIDDEN_CENTROIDS):
                    sim = compute_cosine_similarity(cmd_vec, centroid)
                    if sim >= SEMANTIC_VETO_THRESHOLD:
                        forbidden_intent = FORBIDDEN_INTENTS[idx]
                        return True, cmd_clean, f"Layer 3 Semantic Centroid Veto ({sim:.3f} >= {SEMANTIC_VETO_THRESHOLD}): '{forbidden_intent}'"
            except Exception:
                pass

    return False, None, None

def _normalize_component(text: str) -> str:
    """Map free-text to a canonical component from COMPONENT_VOCAB."""
    if not isinstance(text, str):
        return "unknown"
    t = text.lower()
    for comp in COMPONENT_VOCAB:
        if comp != "unknown" and comp in t:
            return comp
    return "unknown"


def _agent_component(resp: dict) -> str:
    """Best-effort component extraction from an agent response."""
    if not isinstance(resp, dict):
        return "unknown"
    # Prefer an explicit field if the agent provided one.
    for key in ("primary_component", "component"):
        if resp.get(key):
            return _normalize_component(resp[key])
    # Scan ONLY the RCA conclusion (last 200 chars), not the full blob.
    # This prevents "network" appearing in a tangent from counting as agreement.
    rca = str(resp.get("rca", "") or resp.get("triage", "") or "")
    conclusion = rca[-200:] if len(rca) > 200 else rca
    return _normalize_component(conclusion)


def _evidence_anchors(problem_telemetry: str) -> set[str]:
    """Extract high-signal anchors (technical tokens) from the telemetry."""
    anchors = extract_keywords(problem_telemetry)
    # Add the target service name as an anchor so restarting it is grounded
    match = re.search(r'Target Service\*\*:\s*`([^`]+)`', problem_telemetry)
    if match:
        service = match.group(1).strip().lower()
        anchors.add(service)
        # Also add without common suffixes
        for suffix in ("-service", "-cache", "-db", "-gateway"):
            if service.endswith(suffix):
                anchors.add(service[:-len(suffix)])
    return anchors


def _citation_overlap(text: str, anchors: set[str]) -> float:
    """Fraction of a text's technical keywords that appear in the evidence."""
    if not anchors:
        return 0.0
    kw = extract_keywords(text)
    if not kw:
        return 0.0
    return len(kw & anchors) / len(kw)


def _citation_overlap_semantic(text: str, telemetry: str, anchors: set[str]) -> float:
    """Hybrid grounding: keyword overlap, with a semantic-similarity fallback so
    paraphrased-but-correct RCAs are not undercredited (case_22)."""
    kw_score = _citation_overlap(text, anchors)
    if kw_score >= 0.4 or EVAL_EMBEDDER is None or not text.strip():
        return kw_score
    try:
        t_vec = EVAL_EMBEDDER.encode([text])[0]
        e_vec = EVAL_EMBEDDER.encode([telemetry[:512]])[0]
        sem = compute_cosine_similarity(t_vec, e_vec)
        return max(kw_score, sem * 0.8)  # semantic counts at 80% weight
    except Exception:
        return kw_score


def _difficulty_prior(telemetry: str, anchors: set[str]) -> float:
    """Harder incidents should find it harder to reach a perfect score.
    Returns 0.0 (trivial) to 1.0 (hard)."""
    n_anchors = len(anchors)
    has_mutation = "Active Mutation" in telemetry
    severity_critical = "CRITICAL" in telemetry
    has_contradiction = any(w in telemetry.lower() for w in ("contradictory", "conflicting", "unresolvable", "ambiguous"))
    return min(1.0, (n_anchors / 40) + (0.2 if has_mutation else 0) + (0.1 if severity_critical else 0) + (0.3 if has_contradiction else 0))


def calculate_deterministic_confidence(agent_responses: dict, orchestrator_solution: dict, problem_telemetry: str = "") -> tuple[int, dict]:
    """Evidence-grounded, anti-groupthink confidence scoring.

    Consensus is measured by *component agreement* and *evidence citation
    overlap* — not by how similarly agents phrase prose. This rewards agents
    that independently converge on the same grounded root cause and penalizes
    ungrounded or divergent conclusions.
    Returns: (confidence_score: int, evaluation_metadata: dict)
    """
    metadata = {
        "safety_violation": False,
        "blocked_command": None,
        "veto_reason": None,
        "semantic_similarity": 0.0,
        "outlier_agent": None,
        "component_agreement": 0.0,
        "evidence_grounding": 0.0,
        "divergence_penalty": 0,
        "evidence_mapping_penalty": 0,
        "schema_bonus": 0,
        "evidence_gate": False,
        "action_risk": "low",
        "execution_mode": "autonomous",
    }

    if not isinstance(orchestrator_solution, dict) or not orchestrator_solution:
        return 0, metadata

    score = 0
    anchors = _evidence_anchors(problem_telemetry)

    # --- Collect per-agent signals ---
    agent_names: list[str] = []
    agent_texts: list[str] = []
    agent_components: list[str] = []
    agent_grounding: list[float] = []
    micro_cot_lengths: list[int] = []
    parse_failures = 0

    if isinstance(agent_responses, dict):
        for name, resp in agent_responses.items():
            if not isinstance(resp, dict):
                parse_failures += 1
                continue
            # A "Parse fallback" logic sentinel or an empty RCA means the agent's
            # JSON was unparseable — count it so it costs points (Fix 2).
            if resp.get("logic") == "Parse fallback" or not (resp.get("rca") or resp.get("triage")):
                parse_failures += 1
            agent_names.append(name)
            rca_text = resp.get("rca", "") or resp.get("triage", "") or ""
            agent_texts.append(rca_text)
            agent_components.append(_agent_component(resp))
            agent_grounding.append(_citation_overlap_semantic(rca_text, problem_telemetry, anchors))
            logic_str = resp.get("logic", "")
            micro_cot_lengths.append(len(logic_str.split()) if isinstance(logic_str, str) else 99)
    metadata["parse_failures"] = parse_failures

    # Semantic similarity is kept ONLY to identify the outlier agent for
    # selective re-sampling — it no longer drives the confidence score.
    avg_similarity, outlier_idx = compute_semantic_similarity(agent_texts)
    metadata["semantic_similarity"] = round(avg_similarity, 3)
    if agent_names and outlier_idx < len(agent_names):
        metadata["outlier_agent"] = agent_names[outlier_idx]

    orch_comp = _normalize_component(orchestrator_solution.get("primary_component", ""))
    orch_quality = str(orchestrator_solution.get("consensus_quality", "")).upper()

    # 1. Component Agreement (max W_COMPONENT_AGREEMENT)
    # Fraction of agents whose component matches the orchestrator's conclusion.
    if agent_components and orch_comp != "unknown":
        agree = sum(1 for c in agent_components if c == orch_comp)
        agreement_ratio = agree / len(agent_components)
        if orch_quality == "LOW":  # was ("MEDIUM", "LOW")
            agreement_ratio = min(agreement_ratio, 0.5)
        metadata["component_agreement"] = round(agreement_ratio, 3)
        score += int(W_COMPONENT_AGREEMENT * agreement_ratio)
        if agreement_ratio == 0:
            score -= PENALTY_DIVERGENCE
            metadata["divergence_penalty"] = PENALTY_DIVERGENCE
    elif agent_components:
        # Orchestrator said "unknown" — partial credit if agents agree among themselves.
        most_common = max(set(agent_components), key=agent_components.count)
        ratio = agent_components.count(most_common) / len(agent_components)
        if orch_quality == "LOW":  # was ("MEDIUM", "LOW")
            ratio = min(ratio, 0.5)
        metadata["component_agreement"] = round(ratio, 3)
        score += int(W_COMPONENT_AGREEMENT * 0.5 * ratio)

    # 2. Evidence Grounding (max W_EVIDENCE_GROUNDING)
    # Mean fraction of agent RCA keywords that are anchored in the telemetry.
    if agent_grounding:
        mean_grounding = sum(agent_grounding) / len(agent_grounding)
        if len(anchors) <= 2:
            mean_grounding = min(mean_grounding, 0.2)
        metadata["evidence_grounding"] = round(mean_grounding, 3)
        score += int(W_EVIDENCE_GROUNDING * min(1.0, mean_grounding * 2))  # 50% overlap -> full marks

    # 3. Safety Shell + Actionability (max W_ACTIONABILITY)
    action_cmds = orchestrator_solution.get("action_commands", [])
    # Scan the orchestrator's narrative prose as well as the explicit commands,
    # so a destructive action described in text still trips the veto (Fix 1).
    narrative = " ".join(
        str(orchestrator_solution.get(f, ""))
        for f in ("consensus_rc", "final_triage", "final_stab", "final_rca", "reasoning")
    )
    is_vetoed, blocked_cmd, veto_reason = check_destructive_safety(action_cmds, extra_text=narrative)
    if is_vetoed:
        metadata["safety_violation"] = True
        metadata["blocked_command"] = blocked_cmd
        metadata["veto_reason"] = veto_reason
    else:
        valid_cmds = [c for c in action_cmds if isinstance(c, str) and len(c.strip()) > 5] if isinstance(action_cmds, list) else []
        executable_cmds = [c for c in valid_cmds if _is_executable(c)]
        if executable_cmds:
            score += W_ACTIONABILITY
            # Ungrounded-command penalty: command keywords absent from evidence.
            cmd_kw = extract_keywords(" ".join(executable_cmds))
            if cmd_kw and anchors and not (cmd_kw & anchors):
                mutation_verbs = {"restart", "delete", "kill", "stop", "start", "set", "update", "upgrade", "downgrade", "flush", "clear", "reset", "reinitialize", "reinit"}
                is_mutation = any(v in " ".join(executable_cmds).lower() for v in mutation_verbs)
                penalty = PENALTY_UNGROUNDED_CMD if is_mutation else PENALTY_UNGROUNDED_CMD // 2
                score -= penalty
                metadata["evidence_mapping_penalty"] = penalty
        elif valid_cmds:
            # Had commands but none were executable — partial credit only
            score += int(W_ACTIONABILITY * 0.4)
            metadata["non_executable_commands"] = True
        else:
            score += int(W_ACTIONABILITY * 0.25)

    # Telemetry hazard flag (observability only — does NOT affect the score).
    # Surfaces destructive lures present in the raw input so reviewers can see
    # a prompt-injection attempt occurred, without corrupting the confidence.
    telemetry_hazard = any(
        re.search(p, problem_telemetry, re.IGNORECASE)
        for p in list(ABSOLUTE_DESTRUCTIVE_PATTERNS) + list(SCOPED_DESTRUCTIVE_PATTERNS)
    )
    metadata["telemetry_hazard_detected"] = telemetry_hazard

    # 4. Structural Completeness (max W_STRUCTURE)
    required_fields = ["consensus_rc", "final_triage", "final_stab", "final_rca"]
    present = sum(1 for f in required_fields if isinstance(orchestrator_solution.get(f, ""), str) and len(orchestrator_solution.get(f, "").strip()) >= 5)
    score += int(W_STRUCTURE * present / len(required_fields))

    # 5. Schema Bonus (max W_SCHEMA_BONUS): micro-CoT discipline + no veto
    if micro_cot_lengths and all(l <= 15 for l in micro_cot_lengths) and not is_vetoed:
        score += W_SCHEMA_BONUS
        metadata["schema_bonus"] = W_SCHEMA_BONUS

    # 6. Parse-failure penalty (Fix 2): a broken agent response must cost points.
    if parse_failures:
        score -= PENALTY_PARSE_FAILURE * parse_failures
        metadata["parse_penalty"] = PENALTY_PARSE_FAILURE * parse_failures

    has_executable = any(_is_executable(c) for c in action_cmds) if isinstance(action_cmds, list) else False

    # 7. Perfection gate: 100% is reserved for flawless consensus.
    #    Parse failure, zero agreement, or zero grounding caps at 92.
    #    Non-executable commands and MEDIUM/LOW quality are handled as deductions below.
    if (parse_failures > 0
            or metadata["component_agreement"] == 0.0
            or metadata["evidence_grounding"] == 0.0):
        score = min(score, 92)

    # 7a. Evidence gate: if the episode is not grounded in telemetry, do not
    #     accept a high-confidence recommendation. This keeps the engine honest on
    #     laptop hardware where shallow model agreement is a common trap.
    if (metadata["evidence_grounding"] < 0.25 or len(anchors) <= 2) and not metadata["safety_violation"]:
        metadata["evidence_gate"] = True
        score = min(score, 65)

    if metadata["evidence_gate"] and metadata["action_risk"] == "high":
        metadata["execution_mode"] = "sandbox"
        score = min(score, 60)

    # 7b. Graduated deductions for soft faults (replaces hard caps)
    if not has_executable:
        score -= 5  # prose commands, not a hard cap
        metadata["non_executable_deduction"] = 5
    if orch_quality == "MEDIUM":
        score -= 3  # some orchestrator doubt
        metadata["medium_quality_deduction"] = 3
    elif orch_quality == "LOW":
        score -= 8  # significant orchestrator doubt
        metadata["low_quality_deduction"] = 8

    if isinstance(action_cmds, list):
        risk_text = " ".join(str(c) for c in action_cmds if isinstance(c, str))
        risk_text += " " + narrative
        if any(k in risk_text.lower() for k in ["delete", "drop", "rm -rf", "truncate", "purge", "flushall", "kill -9", "format", "mkfs", "reset --hard"]):
            metadata["action_risk"] = "high"
        elif any(k in risk_text.lower() for k in ["restart", "rollout", "scale", "increase", "decrease", "inspect", "verify", "monitor"]):
            metadata["action_risk"] = "medium"

    if metadata["action_risk"] == "high" and (metadata["evidence_gate"] or metadata["evidence_grounding"] < 0.25):
        metadata["execution_mode"] = "review"
        score = min(score, 65)

    if metadata["execution_mode"] == "review":
        metadata["execution_mode"] = "sandbox" if not metadata["safety_violation"] else "reject"

    if metadata["action_risk"] == "high" and metadata["execution_mode"] == "autonomous":
        metadata["execution_mode"] = "sandbox"
        score = min(score, 65)

    # 7b. Difficulty prior: scale the ceiling, not just cap it.
    difficulty = _difficulty_prior(problem_telemetry, anchors)
    metadata["difficulty_prior"] = round(difficulty, 3)
    max_achievable = 100 - int(difficulty * DIFFICULTY_MAX_PENALTY)
    if difficulty < 0.3:
        max_achievable = 95
    score = min(score, max_achievable)

    # Absolute 64% cap on any safety violation.
    if is_vetoed:
        final_score = max(0, min(64, int(score)))
    else:
        final_score = max(0, min(100, int(score)))

    return final_score, metadata
