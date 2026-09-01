"""Output publisher for the Phase 3 -> Phase 4 integration.

The debate's conclusion is published as an `autosre.action.proposed` message to
RabbitMQ for Phase 4. Per the handover brief:
- If any proposed command trips the semantic veto (cosine >= 0.82 against
  FORBIDDEN_CENTROIDS), confidence is capped at 64% and the action routes to
  human review.
- The debate engine itself never executes commands; it only proposes.

RabbitMQ (pika) is optional at import time so the engine still runs in
offline/test mode and can emit the message to stdout or a JSON file instead.
"""

from __future__ import annotations

import json
import os
import uuid
import time
from datetime import datetime, timezone
from typing import Any

from config import BASE_DIR

ROUTING_KEY = "autosre.action.proposed"
EXCHANGE = os.environ.get("AUTOSRE_EXCHANGE", "autosre")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

# Veto cap defined by the integration contract.
VETO_CONFIDENCE_CAP = 64


def format_for_sandbox(message: dict) -> dict:
    """
    Translates the action.proposed envelope into the exact schema expected by Arse_shadow.
    """
    payload = message.get("payload", {})

    # Provide the exact schema the sandbox expects for Layer 2 & Layer 3
    sandbox_payload = {
        "incident_id": message.get("incident_id", "unknown_incident"),
        "problem": message.get("problem", ""),
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
        }
    }
    
    return sandbox_payload


def build_action_proposed(
    incident_id: str,
    result: dict,
    correlation_id: str | None = None,
    fingerprint: str | None = None,
) -> dict:
    """Convert a DebateManager result into the `autosre.action.proposed` envelope.

    `result` is the dict returned by `DebateManager.run_async()`.
    """
    solution = result.get("solution", {}) if isinstance(result, dict) else {}
    # Sometimes debate returns technical_solution under orchestrator
    if not solution and "orchestrator" in result:
        solution = result["orchestrator"].get("technical_solution", {})

    confidence = int(result.get("confidence_score", solution.get("confidence", 0)))
    safety_violation = bool(result.get("safety_violation", solution.get("safety_violation", False)))

    # Contract: veto -> cap at 64% and force human review.
    if safety_violation:
        confidence = min(confidence, VETO_CONFIDENCE_CAP)

    execution_tier = result.get("execution_tier", "TIER_2_SHADOW_SANDBOX")
    if safety_violation:
        execution_tier = "HUMAN_REVIEW"

    action_commands = solution.get("action_commands", [])
    if not isinstance(action_commands, list):
        action_commands = [str(action_commands)] if action_commands else []

    return {
        "event_type": "action.proposed",
        "incident_id": incident_id,
        "problem": result.get("problem", ""),  # Injected directly so publish() can use it
        "correlation_id": correlation_id or str(uuid.uuid4()),
        "fingerprint": fingerprint,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "consensus_rc": solution.get("consensus_rc", solution.get("final_rca", "")),
            "primary_component": solution.get("primary_component", "unknown"),
            "action_commands": action_commands,
            "runbook": solution.get("final_rca") or solution.get("final_triage") or "",
            "confidence": confidence,
            "execution_tier": execution_tier,
            "safety_violation": safety_violation,
            "veto_reason": (solution.get("scoring_metadata") or {}).get("veto_reason"),
            "consensus_quality": solution.get("consensus_quality", "LOW"),
            "round_2_executed": result.get("round_2_executed", False),
            "total_latency_seconds": result.get("total_latency_seconds"),
        },
    }


class ActionPublisher:
    """Publishes `autosre.action.proposed` to RabbitMQ, with offline fallbacks and Sandbox routing."""

    def __init__(self, rabbitmq_url: str = RABBITMQ_URL, exchange: str = EXCHANGE):
        self.rabbitmq_url = rabbitmq_url
        self.exchange = exchange

    def publish_to_sandbox(self, message: dict) -> dict:
        """
        Writes the formatted JSON atomically to the Arse_shadow sandbox inputs folder.
        """
        sandbox_payload = format_for_sandbox(message)
        incident_id = sandbox_payload.get("incident_id", "unknown_incident")
        timestamp = int(time.time())
        filename = f"{incident_id}_{timestamp}.json"
        
        # Resolve path: debate/action_publisher.py -> Smart horizon hackathon/Arse_shadow/...
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

    def publish(self, message: dict, offline_dir: str | None = None) -> dict:
        """Publish the message. Returns {'transport': ..., 'ok': bool, 'detail': ...}.

        Always drops the payload into the Shadow Sandbox first, then tries RabbitMQ/offline.
        """
        
        # 1. ALWAYS Trigger the Sandbox Drop
        try:
            self.publish_to_sandbox(message)
        except Exception as e:
            print(f"[ACTION_PUBLISHER] Failed to drop to sandbox: {e}")

        # 2. Proceed with standard RabbitMQ/Offline publishing
        body = json.dumps(message, ensure_ascii=False, default=str)

        # Attempt live publish.
        try:
            import pika  # type: ignore

            params = pika.URLParameters(self.rabbitmq_url)
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.exchange_declare(exchange=self.exchange, exchange_type="topic", durable=True)
            channel.basic_publish(
                exchange=self.exchange,
                routing_key=ROUTING_KEY,
                body=body.encode("utf-8"),
                properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
            )
            connection.close()
            return {"transport": "rabbitmq", "ok": True, "detail": ROUTING_KEY}
        except ImportError:
            pass
        except Exception as e:  # broker down / unreachable -> offline fallback
            detail = f"rabbitmq unavailable ({e}); falling back to file"
            return self._write_offline(message, offline_dir, detail)

        return self._write_offline(message, offline_dir, "pika not installed; offline mode")

    @staticmethod
    def _write_offline(message: dict, offline_dir: str | None, detail: str) -> dict:
        out_dir = offline_dir or os.path.join(BASE_DIR, "output", "proposed_actions")
        os.makedirs(out_dir, exist_ok=True)
        incident_id = message.get("incident_id", "incident")
        path = os.path.join(out_dir, f"{incident_id}.action_proposed.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(message, f, ensure_ascii=False, indent=2, default=str)
        return {"transport": "file", "ok": True, "detail": f"{detail} -> {path}"}