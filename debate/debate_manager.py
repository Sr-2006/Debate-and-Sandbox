import time
import asyncio
from orchestrator import Orchestrator
from agents.optimist import Optimist
from agents.critic import Critic
from agents.fact_checker import FactChecker

from logger import DebateLogger
from incident_parser import IncidentParser
from json_utils import safe_parse_json
from scoring import calculate_deterministic_confidence, warmup_scoring_engine, extract_keywords
from config import (
    AUTONOMOUS_THRESHOLD,
    SANDBOX_THRESHOLD,
    LATENCY_TARGET,
    LATENCY_GRACE_PERIOD,
)

class DebateManager:
    """Coordinates parallel async agent reasoning, high-dimensional semantic scoring, defensive shell linter, and 3-tier routing."""
    
    def __init__(self):
        # 1. Instant-On Boot Warmup
        warmup_scoring_engine()
        self.optimist = Optimist()
        self.critic = Critic()
        self.fact_checker = FactChecker()
        self.orchestrator = Orchestrator()
        self.agents_map = {
            "optimist": self.optimist,
            "critic": self.critic,
            "fact_checker": self.fact_checker
        }

    @staticmethod
    def safe_parse_json(text: str) -> dict:
        """Robust JSON repair for agent responses (delegates to shared util)."""
        return safe_parse_json(text, fallback={"logic": "Parse fallback", "triage": text or "", "stab": "", "rca": ""})

    async def initial_analysis_detailed_async(self, problem: str) -> dict:
        """Run Round 1 independent reasoning concurrently using asyncio.gather()."""
        results = await asyncio.gather(
            self.optimist.think_detailed_async(problem),
            self.critic.think_detailed_async(problem),
            self.fact_checker.think_detailed_async(problem)
        )

        return {
            "optimist": results[0],
            "critic": results[1],
            "fact_checker": results[2]
        }

    async def run_async(self, problem: str | dict) -> dict:
        """Execute debate pipeline with Hardened Pipeline Order, Three-Layer Defensive Shell, and 3-tier decision tree."""
        # 1. Timer Placement: Exclude initial parsing
        formatted_problem, inc_id = IncidentParser.parse_and_format(problem)
        pipeline_start = time.perf_counter()
        
        logger = DebateLogger()
        logger.log_problem(problem, incident_id=inc_id)

        print(f"\n=== Incident Analysis: [{inc_id}] (Defensive Shell Active) ===")

        max_iterations = 2
        round_2_executed = False
        r1_time = 0.0
        r2_time = 0.0
        
        agent_responses = {}
        r1_detailed = {}
        execution_tier = "TIER_3_RE_ITERATION"

        for iteration in range(1, max_iterations + 1):
            iter_start = time.perf_counter()
            print(f"\n=== Iteration {iteration}: Agent Analysis (Native JSON & Micro-CoT) ===")

            if iteration == 1:
                # Early-consensus fast path: launch agents as tasks so we can
                # cancel the third if the first two already agree strongly.
                tasks = {
                    "optimist": asyncio.create_task(self.optimist.think_detailed_async(formatted_problem)),
                    "critic": asyncio.create_task(self.critic.think_detailed_async(formatted_problem)),
                    "fact_checker": asyncio.create_task(self.fact_checker.think_detailed_async(formatted_problem)),
                }
                done, pending = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)

                fast_path = False
                if len(done) >= 2:
                    completed = [t.result() for t in done]
                    parsed_pair = [self.safe_parse_json(m["response"]) for m in completed]
                    kw_sets = [extract_keywords(p.get("rca", "") or p.get("triage", "")) for p in parsed_pair]
                    if len(kw_sets) == 2 and kw_sets[0] and kw_sets[1]:
                        overlap = len(kw_sets[0] & kw_sets[1]) / max(1, len(kw_sets[0] | kw_sets[1]))
                        if overlap > 0.6:
                            fast_path = True
                            for t in pending:
                                t.cancel()
                            print(f"[Fast Path] Early consensus detected (overlap={overlap:.2f}). Skipping third agent.")

                if fast_path:
                    r1_detailed = {name: t.result() for name, t in tasks.items() if t in done}
                else:
                    r1_detailed = {name: await t for name, t in tasks.items()}

                r1_time = time.perf_counter() - iter_start

                for agent_name, meta in r1_detailed.items():
                    parsed_json = self.safe_parse_json(meta["response"])
                    agent_responses[agent_name] = parsed_json
                    logger.log_round1(
                        agent_name=agent_name,
                        prompt=meta["prompt"],
                        response=parsed_json,
                        latency=meta["latency"]
                    )
            else:
                # Iteration 2: Selective Re-sampling of Outlier Agent
                round_2_executed = True
                outlier_name = meta_info.get("outlier_agent") or "critic"
                print(f"[Selective Re-sampling] Re-running outlier agent: {outlier_name.upper()}")

                failed_rc = orchestration_result["solution"].get("consensus_rc", "Unknown Root Cause")

                # Build Peer Reasoning & Conflict Summary (for context only)
                peer_summaries = []
                for p_name, p_resp in agent_responses.items():
                    if p_name != outlier_name:
                        peer_summaries.append(f"{p_name.upper()}: {p_resp.get('rca', '')}")

                conflict_summary = " | ".join(peer_summaries)

                # Anchored revision: force the outlier to re-ground in the raw
                # telemetry rather than defer to peer opinion (breaks echo chambers).
                guided_revision_prompt = (
                    f"{formatted_problem}\n\n"
                    f"### REVISION CONSTRAINT (Iteration 2)\n"
                    f"- **Your previous hypothesis was an outlier**: {failed_rc}\n"
                    f"- **Peer hypotheses (context only, do NOT defer to them)**: {conflict_summary}\n"
                    f"- **Instruction**: Re-derive your root cause STRICTLY from the telemetry evidence above. "
                    f"Cite the specific log line or metric that supports your conclusion. "
                    f"If the evidence supports your original hypothesis, keep it and justify with the citation. "
                    f"Only pivot if the evidence points elsewhere."
                )

                outlier_agent_obj = self.agents_map.get(outlier_name, self.critic)
                outlier_meta = await outlier_agent_obj.think_detailed_async(guided_revision_prompt)
                parsed_outlier = self.safe_parse_json(outlier_meta["response"])
                
                # Update only outlier response
                agent_responses[outlier_name] = parsed_outlier
                r2_time = time.perf_counter() - iter_start
                
                logger.log_round1(
                    agent_name=f"{outlier_name}_revised",
                    prompt=outlier_meta["prompt"],
                    response=parsed_outlier,
                    latency=outlier_meta["latency"]
                )

            # Extract lean problem header for Orchestrator
            header_lines = [line for line in formatted_problem.splitlines() if line.startswith("- **Target Service**") or line.startswith("### INCIDENT CONTEXT")]
            lean_header = "\n".join(header_lines) if header_lines else f"Incident ID: {inc_id}"

            # Orchestrator Synthesis
            print(f"\n=== Orchestrator Synthesis (Iteration {iteration}) ===")
            orch_start = time.perf_counter()
            orchestration_result = await self.orchestrator.synthesize_detailed_async(lean_header, agent_responses)
            orch_time = time.perf_counter() - orch_start

            # STEP 1: Calculate Raw Deterministic Confidence Score & Safety Veto Check
            calc_confidence, meta_info = calculate_deterministic_confidence(
                agent_responses,
                orchestration_result["solution"],
                problem_telemetry=formatted_problem
            )

            # STEP 2: Latency SLO tracking (observability only — NOT a score penalty).
            # The old MTTR penalty subtracted points for slowness, which pushed
            # borderline cases below the sandbox threshold and triggered Round 2,
            # making the pipeline even slower (a doom loop). Latency is now reported
            # against the target but never reduces confidence.
            current_latency = time.perf_counter() - pipeline_start
            slo_breach = current_latency > (LATENCY_TARGET + LATENCY_GRACE_PERIOD)
            if slo_breach:
                print(f"[LATENCY SLO] {current_latency:.2f}s exceeds target {LATENCY_TARGET}s (reported, not penalized).")

            # STEP 3: Safety Veto Calibration (64% Absolute Cap)
            if meta_info["safety_violation"]:
                calc_confidence = max(0, min(64, calc_confidence))
                print(f"[DEFENSIVE SHELL VETO] {meta_info['veto_reason']}. Command: '{meta_info['blocked_command']}'. Capped at {calc_confidence}%")

            orchestration_result["solution"]["calculated_confidence"] = calc_confidence
            orchestration_result["solution"]["safety_violation"] = meta_info["safety_violation"]
            # Persist full scoring metadata so the JSON output is self-explanatory
            # (component agreement, grounding, penalties, difficulty prior, hazard flag).
            orchestration_result["solution"]["scoring_metadata"] = meta_info

            print(f"[Semantic Scoring] Iteration {iteration} Calibrated Score: {calc_confidence}% | Safety Veto: {meta_info['safety_violation']} | Similarity: {meta_info['semantic_similarity']}")

            # STEP 4: Decision Tree Routing (Evaluated AFTER Penalties & Veto Calibration)
            if meta_info["safety_violation"]:
                execution_tier = "TIER_2_SHADOW_SANDBOX"
                print("[TIER 2] Shadow Sandbox Routing (Safety Veto Triggered)")
                break
            elif calc_confidence >= AUTONOMOUS_THRESHOLD:
                execution_tier = "TIER_1_AUTONOMOUS_EXECUTION"
                print(f"[TIER 1] Autonomous Execution Approved ({calc_confidence}% >= {AUTONOMOUS_THRESHOLD}%)")
                break
            elif calc_confidence >= SANDBOX_THRESHOLD:
                execution_tier = "TIER_2_SHADOW_SANDBOX"
                print(f"[TIER 2] Shadow Sandbox Routing ({calc_confidence}% in [{SANDBOX_THRESHOLD}, {AUTONOMOUS_THRESHOLD - 1}])")
                break
            else:
                execution_tier = "TIER_3_RE_ITERATION"
                print(f"[TIER 3] Re-iteration Triggered ({calc_confidence}% < {SANDBOX_THRESHOLD}%)")

            if iteration == max_iterations:
                # If iteration 2 finishes < 65%, route final output to Sandbox
                execution_tier = "TIER_2_SHADOW_SANDBOX"
                break

        total_latency = time.perf_counter() - pipeline_start

        logger.log_consensus(score=calc_confidence / 100.0, threshold=AUTONOMOUS_THRESHOLD / 100.0, debate_required=round_2_executed)
        logger.log_orchestrator(
            prompt=orchestration_result["prompt"],
            technical_solution=orchestration_result["solution"],
            confidence=calc_confidence,
            latency=orch_time
        )

        logger.log_latency(
            round1_time=r1_time,
            round2_time=r2_time,
            orchestrator_time=orch_time,
            total_pipeline_time=total_latency
        )

        json_path, md_path = logger.save()

        return {
            "solution": orchestration_result["solution"],
            "confidence_score": calc_confidence,
            "execution_tier": execution_tier,
            "safety_violation": meta_info["safety_violation"],
            "consensus_score": round(calc_confidence / 100.0, 2),
            "round_2_executed": round_2_executed,
            "total_latency_seconds": round(total_latency, 2),
            "agent_responses": agent_responses,
            "json_log_path": json_path,
            "md_log_path": md_path
        }

    def run(self, problem: str | dict) -> dict:
        """Synchronous wrapper for run_async."""
        return asyncio.run(self.run_async(problem))
