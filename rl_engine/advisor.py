import uuid
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from rl_engine.config import RL_OPERATING_MODE, RL_POLICY_NAME, RL_POLICY_VERSION, RL_FEATURE_VERSION, RL_MIN_CAPABILITY_EPISODES
from rl_engine.contracts import RLAdvisoryData, PolicyRef, ProposalRef, FeatureSnapshot
from rl_engine.feature_extractor import extract_features
from rl_engine.bayesian_prior import get_bayesian_prior
from rl_engine.safety_mask import get_allowed_actions
from rl_engine.model_store import ModelStore
from contracts.validation import get_capabilities, is_mvp_supported




class RLAdvisor:
    """Safe Contextual-Bandit Advisor for Phase 3 -> Phase 4 routing recommendations."""

    def __init__(self, model_version: str = "promoted", operating_mode: str = RL_OPERATING_MODE):
        self.model_store = ModelStore()
        self.operating_mode = operating_mode
        self.policy, self.model_meta = self.model_store.load_model(model_version)
        self.model_version = self.model_meta.get("model_version", "cold-start")

    def generate_advisory(self, envelope: Dict[str, Any], p3_res: Optional[Dict[str, Any]] = None, run_id: Optional[str] = None) -> RLAdvisoryData:
        start_time = time.perf_counter()
        advisory_id = f"adv_{uuid.uuid4().hex[:12]}"
        effective_run_id = run_id or f"run_{int(time.time() * 1000)}"

        try:
            env_safe = envelope if isinstance(envelope, dict) else {}
            incident_id = env_safe.get("incident_id", "case_unknown")

            intents = env_safe.get("intents", [])
            first_intent = intents[0] if (intents and isinstance(intents, list) and isinstance(intents[0], dict)) else {}
            intent_type = first_intent.get("intent_type", "NO_SUPPORTED_ACTION")
            mode = first_intent.get("mode", "OBSERVE")
            risk_class = first_intent.get("risk_class", "LOW")
            requires_human = bool(first_intent.get("requires_human_approval", False))
            target_ref = first_intent.get("target_ref") or env_safe.get("target_ref") or {}
            target_kind = target_ref.get("kind", "container")

            p3_conf = env_safe.get("phase3_confidence", {})
            score = p3_conf.get("score") if isinstance(p3_conf, dict) else p3_conf
            confidence = float(score) if score is not None else 0.0

            safety_violation = bool(env_safe.get("safety_violation", False))
            evidence_refs = env_safe.get("evidence_refs") or first_intent.get("evidence_refs") or []

            # Single source of capability truth from catalog
            capabilities = get_capabilities()
            mvp_supported = is_mvp_supported(intent_type)
            capability_mapped = (intent_type in capabilities) and (intent_type != "NO_SUPPORTED_ACTION")

            # Target resolution evaluation
            canonical_target = target_ref.get("canonical_name")
            target_resolved = bool(
                canonical_target
                and str(canonical_target).lower() not in {"unknown", "unknown-service", "none", "n/a", ""}
            )

            # Query Bayesian Prior
            lower_bound, sample_size, successes, failures = get_bayesian_prior(intent_type, target_kind)

            # Feature Extraction
            feat_dict, feat_vector, feat_hash = extract_features(
                envelope=envelope,
                beta_lower_bound=lower_bound,
                sample_size=sample_size
            )

            # Deterministic Safety Mask
            p3_status = "PHASE3_FAILED" if p3_res and p3_res.get("status") == "PHASE3_FAILED" else "SUCCESS"
            allowed_actions, mask_reasons = get_allowed_actions(
                p3_status=p3_status,
                confidence=confidence,
                safety_violation=safety_violation,
                mode=mode,
                human_approval=requires_human,
                capability_mapped=capability_mapped,
                mvp_supported=mvp_supported,
                evidence_refs=evidence_refs,
                target_resolved=target_resolved
            )

            # Feature schema compatibility check with loaded model
            model_feat_ver = self.model_meta.get("feature_schema_version", RL_FEATURE_VERSION)
            schema_mismatch = (self.model_version != "cold-start") and (model_feat_ver != RL_FEATURE_VERSION)

            is_cold_start = (self.model_version == "cold-start") or (sample_size < RL_MIN_CAPABILITY_EPISODES) or schema_mismatch
            reason_codes = list(mask_reasons)

            if schema_mismatch:
                reason_codes.append("MODEL_FEATURE_SCHEMA_MISMATCH")
                reason_codes.append("RL_COLD_START")
            elif is_cold_start:
                reason_codes.append("INSUFFICIENT_REAL_OUTCOMES")
                reason_codes.append("RL_COLD_START")

            if is_cold_start:
                # Deterministic Cold-Start Rules
                if len(allowed_actions) == 1:
                    recommendation = allowed_actions[0]
                elif confidence < 0.50:
                    recommendation = "OBSERVE_FIRST" if "OBSERVE_FIRST" in allowed_actions else "ABSTAIN"
                elif safety_violation or requires_human or mode == "MUTATE_HIGH_RISK":
                    recommendation = "REQUIRE_HUMAN_REVIEW" if "REQUIRE_HUMAN_REVIEW" in allowed_actions else "ABSTAIN"
                elif mvp_supported and mode == "MUTATE_REVERSIBLE":
                    recommendation = "OBSERVE_FIRST" if "OBSERVE_FIRST" in allowed_actions else "ABSTAIN"
                elif mvp_supported and mode == "OBSERVE":
                    recommendation = "ACCEPT_PROPOSAL" if "ACCEPT_PROPOSAL" in allowed_actions else "ABSTAIN"
                else:
                    recommendation = "ABSTAIN"

                scores = {act: (0.5 if act == recommendation else 0.1) for act in ["ACCEPT_PROPOSAL", "OBSERVE_FIRST", "REQUIRE_HUMAN_REVIEW", "ABSTAIN"]}
                uncertainty = 0.5
            else:
                recommendation, scores, uncertainty = self.policy.predict(feat_vector, allowed_actions)

            # Influence rule: SHADOW mode never allows influence
            influence_allowed = (self.operating_mode == "ADVISORY") and (not is_cold_start)

            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

            snapshot = FeatureSnapshot(
                feature_schema_version=RL_FEATURE_VERSION,
                features=feat_dict,
                feature_vector=feat_vector,
                feature_hash=feat_hash
            )

            return RLAdvisoryData(
                schema_version="1.0",
                advisory_id=advisory_id,
                incident_id=incident_id,
                run_id=effective_run_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                policy=PolicyRef(
                    policy_name=RL_POLICY_NAME,
                    policy_version=RL_POLICY_VERSION,
                    model_version=self.model_version,
                    operating_mode=self.operating_mode
                ),
                proposal=ProposalRef(
                    intent_type=intent_type,
                    target_kind=target_kind,
                    mode=mode,
                    risk_class=risk_class
                ),
                recommendation=recommendation,
                action_scores=scores,
                uncertainty=uncertainty,
                sample_size=sample_size,
                cold_start=is_cold_start,
                influence_allowed=influence_allowed,
                reason_codes=reason_codes,
                feature_schema_version=RL_FEATURE_VERSION,
                feature_hash=feat_hash,
                latency_ms=latency_ms,
                estimated_success_probability=round(lower_bound, 4),
                feature_snapshot=snapshot
            )



        except Exception as e:
            # Fail-open fallback
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return RLAdvisoryData(
                schema_version="1.0",
                advisory_id=advisory_id,
                incident_id=incident_id,
                run_id=effective_run_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                policy=PolicyRef(
                    policy_name=RL_POLICY_NAME,
                    policy_version=RL_POLICY_VERSION,
                    model_version="fallback",
                    operating_mode=self.operating_mode
                ),
                proposal=ProposalRef(
                    intent_type="unknown",
                    target_kind="unknown",
                    mode="UNKNOWN",
                    risk_class="LOW"
                ),
                recommendation="ABSTAIN",
                action_scores={"ACCEPT_PROPOSAL": None, "OBSERVE_FIRST": None, "REQUIRE_HUMAN_REVIEW": None, "ABSTAIN": 0.0},
                uncertainty=1.0,
                sample_size=0,
                cold_start=True,
                influence_allowed=False,
                reason_codes=["RL_ADVISOR_UNAVAILABLE", str(e)],
                feature_schema_version=RL_FEATURE_VERSION,
                feature_hash="0000000000000000000000000000000000000000000000000000000000000000",
                latency_ms=latency_ms,
                estimated_success_probability=None
            )
