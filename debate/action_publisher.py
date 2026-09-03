from __future__ import annotations

import json
import os
import uuid
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from debate.config import BASE_DIR

from contracts.canonical_json import canonicalize_json, compute_payload_hash
from contracts.models import ActionProposedV2Envelope, Intent, TargetRef, SourceRef, Phase3Confidence
from contracts.validation import validate_envelope, ReasonCode

ROUTING_KEY_V2 = "autosre.action.proposed.v2"
EXCHANGE = os.environ.get("AUTOSRE_EXCHANGE", "autosre")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

VETO_CONFIDENCE_CAP = 64

def format_for_sandbox(message: dict) -> dict:
    """Translates v2 or legacy message envelope into the schema expected by Arse_shadow."""
    if message.get("schema_version") == "2.0":
        intents = message.get("intents", [])
        action_cmds = []
        for i in intents:
            params = i.get("parameters", {})
            action_cmds.append(f"{i.get('intent_type')}: {params}")

        conf_score = int(message.get("phase3_confidence", {}).get("score", 0.85) * 100)
        return {
            "schema_version": "2.0",
            "incident_id": message.get("incident_id", "unknown_incident"),
            "problem": message.get("problem_summary", ""),
            "payload_hash": message.get("payload_hash", compute_payload_hash(message)),
            "orchestrator": {
                "technical_solution": {
                    "action_commands": action_cmds,
                    "confidence": conf_score,
                    "safety_violation": message.get("safety_violation", False)
                }
            },
            "root_cause_analysis": {
                "summary": message.get("human_summary", "")
            },
            "raw_v2_envelope": message
        }

    payload = message.get("payload", {})
    return {
        "incident_id": message.get("incident_id", "unknown_incident"),
        "problem": message.get("problem", ""),
        "payload_hash": compute_payload_hash(message),
        "orchestrator": {
            "technical_solution": {
                "action_commands": payload.get("action_commands", []),
                "confidence": payload.get("confidence", 0),
                "calculated_confidence": payload.get("confidence", 0),
                "safety_violation": payload.get("safety_violation", False)
            }
        },
        "root_cause_analysis": {
            "summary": payload.get("consensus_rc", "")
        },
        "raw_envelope": message
    }


def build_action_proposed(
    incident_id: str,
    result: dict,
    correlation_id: str | None = None,
    fingerprint: str | None = None,
) -> dict:
    """Convert DebateManager result into v2 envelope dict format with validation."""
    solution = result.get("solution", {}) if isinstance(result, dict) else {}
    if not solution and "orchestrator" in result:
        solution = result["orchestrator"].get("technical_solution", {})

    # Fix envelope confidence precedence: deterministic confidence_score is authoritative
    conf_raw = result.get("confidence_score") if isinstance(result, dict) else 0.0
    if conf_raw is None:
        conf_raw = 0.0

    model_claimed = solution.get("confidence")
    model_claimed_float = None
    if model_claimed is not None:
        try:
            model_claimed_float = float(model_claimed) / 100.0 if float(model_claimed) > 1.0 else float(model_claimed)
        except Exception:
            model_claimed_float = None

    conf_float = 0.0
    if conf_raw is not None:
        try:
            conf_float = float(conf_raw) / 100.0 if float(conf_raw) > 1.0 else float(conf_raw)
        except Exception:
            conf_float = 0.0

    if result.get("phase3_status") == "PHASE3_FAILED":
        conf_float = 0.0

    conf_int = int(conf_float * 100)

    safety_violation = bool(result.get("safety_violation", solution.get("safety_violation", False)))
    if safety_violation:
        conf_int = min(conf_int, VETO_CONFIDENCE_CAP)
        conf_float = min(conf_float, VETO_CONFIDENCE_CAP / 100.0)



    execution_tier = result.get("execution_tier", "TIER_1_AUTONOMOUS_EXECUTION")
    if safety_violation:
        execution_tier = "HUMAN_REVIEW"

    orig_prob = result.get("original_problem")
    raw_target = None
    if isinstance(orig_prob, dict):
        raw_target = orig_prob.get("incident_event", {}).get("target_service") or orig_prob.get("target_service")
    elif isinstance(orig_prob, str):
        import re
        match = re.search(r"Target Service\*\*:\s*([^\s\n\r]+)", orig_prob)
        if match:
            raw_target = match.group(1)

    target_name = solution.get("primary_component") or solution.get("target_service") or raw_target
    if target_name and target_name.lower() in ["unknown", "unknown-service", "n/a", "none"]:
        target_name = raw_target


    problem_text = result.get("problem") or solution.get("problem_summary") or result.get("normalized_incident") or "Incident reported"
    if isinstance(problem_text, dict):
        problem_text = json.dumps(problem_text)




    
    # Extract evidence anchors directly from solution without fabrication
    raw_ev = solution.get("evidence") or solution.get("evidence_refs") or result.get("evidence_refs")
    if isinstance(raw_ev, str):
        evidence_anchors = [raw_ev] if raw_ev.strip() else []
    elif isinstance(raw_ev, list):
        evidence_anchors = [str(x) for x in raw_ev if str(x).strip()]
    else:
        evidence_anchors = []

    single_intent = solution.get("intent")
    intents_raw = solution.get("intents", [])
    if single_intent and isinstance(single_intent, dict):
        intents_raw = [single_intent]

    intents: List[Intent] = []

    if intents_raw and isinstance(intents_raw, list):
        for idx, item in enumerate(intents_raw):
            if isinstance(item, dict):
                t_ref = item.get("target_ref", {})
                t_kind = t_ref.get("kind") if isinstance(t_ref, dict) else item.get("target_kind", "container")
                t_name = t_ref.get("canonical_name") if isinstance(t_ref, dict) else target_name
                intent_type = item.get("intent_type", "NO_SUPPORTED_ACTION")
                mode = item.get("mode", "OBSERVE")
                intents.append(Intent(
                    intent_id=item.get("intent_id", f"int_{idx}_{uuid.uuid4().hex[:6]}"),
                    intent_type=intent_type,
                    mode=mode,
                    target_ref=TargetRef(kind=t_kind or "container", canonical_name=t_name or target_name),
                    parameters=item.get("parameters", {}),
                    evidence_refs=item.get("evidence_refs", evidence_anchors),
                    preconditions=item.get("preconditions", []),
                    postconditions=item.get("postconditions", []),
                    timeout_seconds=item.get("timeout_seconds", 30),
                    max_attempts=item.get("max_attempts", 1),
                    risk_class=item.get("risk_class", "MEDIUM" if mode != "OBSERVE" else "LOW"),
                    requires_human_approval=item.get("requires_human_approval", safety_violation)
                ))

    if not intents:
        intents.append(Intent(
            intent_id=f"int_0_{uuid.uuid4().hex[:6]}",
            intent_type="NO_SUPPORTED_ACTION",
            mode="OBSERVE",
            target_ref=TargetRef(kind="container", canonical_name=target_name),
            parameters={},
            evidence_refs=evidence_anchors,
            preconditions=[],
            postconditions=[],
            timeout_seconds=30,
            max_attempts=1,
            risk_class="LOW",
            requires_human_approval=safety_violation
        ))




    env = ActionProposedV2Envelope.create_default(
        incident_id=incident_id,
        problem_summary=problem_text,
        target_name=target_name,
        intents=intents,
        confidence=conf_float,
        correlation_id=correlation_id,
        evidence_refs=evidence_anchors
    )

    dict_repr = {
        "schema_version": env.schema_version,
        "event_id": env.event_id,
        "event_type": "autosre.action.proposed",
        "incident_id": incident_id,
        "correlation_id": env.correlation_id,
        "fingerprint": fingerprint or env.fingerprint,
        "created_at": env.created_at,
        "source": {
            "phase": env.source.phase,
            "code_commit": env.source.code_commit,
            "model_name": env.source.model_name
        },
        "problem_summary": env.problem_summary,
        "target_ref": {
            "kind": env.target_ref.kind,
            "canonical_name": env.target_ref.canonical_name,
            "shadow_alias": env.target_ref.shadow_alias
        },
        "phase3_confidence": {
            "score": env.phase3_confidence.score,
            "uncertainty": env.phase3_confidence.uncertainty,
            "calibration_status": env.phase3_confidence.calibration_status
        },
        "execution_tier": execution_tier,
        "safety_violation": safety_violation,
        "evidence_refs": env.evidence_refs,
        "intents": [
            {
                "intent_id": i.intent_id,
                "intent_type": i.intent_type,
                "mode": i.mode,
                "target_ref": {
                    "kind": i.target_ref.kind,
                    "canonical_name": i.target_ref.canonical_name
                },
                "parameters": i.parameters,
                "evidence_refs": i.evidence_refs,
                "preconditions": i.preconditions,
                "postconditions": i.postconditions,
                "timeout_seconds": i.timeout_seconds,
                "max_attempts": i.max_attempts,
                "risk_class": i.risk_class,
                "requires_human_approval": i.requires_human_approval
            } for i in env.intents
        ],
        "human_summary": env.human_summary
    }

    if result.get("rl_advisory"):
        dict_repr["rl_advisory"] = result.get("rl_advisory")

    dict_repr["payload_hash"] = compute_payload_hash(dict_repr)

    
    # Enforce schema validation before returning/publishing
    is_valid, errors, reason_code = validate_envelope(dict_repr)
    if not is_valid:
        raise ValueError(f"Envelope validation failed ({reason_code.value}): {errors}")

    return dict_repr


class ActionPublisher:
    """Publishes `autosre.action.proposed.v2` with payload hashing & single-delivery guarantees."""

    def __init__(self, rabbitmq_url: str = RABBITMQ_URL, exchange: str = EXCHANGE):
        self.rabbitmq_url = rabbitmq_url
        self.exchange = exchange

    def publish_to_sandbox(self, message: dict) -> dict:
        """Writes formatted JSON atomically to the Arse_shadow sandbox inputs folder."""
        sandbox_payload = message
        incident_id = sandbox_payload.get("incident_id", "unknown_incident")

        timestamp = int(time.time())
        filename = f"{incident_id}_{timestamp}.json"
        
        base_dir = os.path.dirname(os.path.abspath(__file__))
        sandbox_input_dir = os.path.abspath(os.path.join(
            base_dir, "..", "Arse_shadow", "shadow_sandbox", "sample_inputs"
        ))
        
        os.makedirs(sandbox_input_dir, exist_ok=True)
        temp_path = os.path.join(sandbox_input_dir, f"{filename}.tmp")
        final_path = os.path.join(sandbox_input_dir, filename)

        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(sandbox_payload, f, indent=4, ensure_ascii=False)
            
        os.rename(temp_path, final_path)
        print(f"\n[ACTION_PUBLISHER] Successfully dropped fix into Sandbox: {final_path}\n")
        
        return {"transport": "sandbox_dir", "ok": True, "detail": final_path}

    def _write_offline(self, message: dict, offline_dir: str | None, detail: str) -> dict:
        out_dir = offline_dir or os.path.join(BASE_DIR, "output", "proposed_actions")
        os.makedirs(out_dir, exist_ok=True)
        incident_id = message.get("incident_id", "incident")
        path = os.path.join(out_dir, f"{incident_id}.action_proposed.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(message, f, ensure_ascii=False, indent=2, default=str)
        return {"transport": "file", "ok": True, "detail": f"{detail} -> {path}"}

    def publish(self, message: dict, offline_dir: str | None = None) -> dict:
        """Single delivery publish strategy: delivers to ONE transport target."""
        if offline_dir:
            return self._write_offline(message, offline_dir, "offline mode")

        # 1. RabbitMQ live publish if available
        body = canonicalize_json(message)
        try:
            import pika  # type: ignore
            params = pika.URLParameters(self.rabbitmq_url)
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.exchange_declare(exchange=self.exchange, exchange_type="topic", durable=True)
            channel.basic_publish(
                exchange=self.exchange,
                routing_key=ROUTING_KEY_V2,
                body=body.encode("utf-8"),
                properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
            )
            connection.close()
            return {"transport": "rabbitmq", "ok": True, "detail": ROUTING_KEY_V2}
        except (ImportError, Exception):
            pass

        # 2. Direct Sandbox delivery if available
        try:
            return self.publish_to_sandbox(message)
        except Exception:
            pass

        # 3. File fallback single delivery
        return self._write_offline(message, offline_dir, "offline mode")