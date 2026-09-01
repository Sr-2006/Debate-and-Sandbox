"""Evidence loader for the Phase 1/2 -> Phase 3 debate integration.

Phase 3 receives a per-incident `debate_evidence/<incident_id>/` folder that the
upstream orchestrator assembles before spinning up the debate. This module turns
that folder (or an enriched AMQP payload, or a `unified_master_dataset.json`
entry) into the exact dict payload that `DebateManager.run_async()` accepts.

Contract guarantees this loader relies on (from phase1_schema.py / handover brief):
- `incidents[]` is pre-sorted descending by `priority_score` (index 0 = hottest).
- `incident_id` always matches ``^[a-zA-Z0-9_-]+_\\d+$`` (never a random UUID).
- `log_cluster_template` is never blank and never a bare `at <*>` stack fragment.
- `metrics_snapshot` may be ``[]`` for fresh clusters -> handled gracefully.
- `metadata` carries `dataset_version` and `git_sha` for reproducibility.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# The six canonical evidence blocks the debate engine understands.
CANONICAL_BLOCKS = (
    "system_context",
    "incident_event",
    "infrastructure_topology",
    "service_health_status",
    "telemetry_evidence",
    "injected_chaos_context",
)

INCIDENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+_\d+$")


class EvidenceLoader:
    """Assembles engine-ready incident payloads from Phase 1/2 evidence sources."""

    # ------------------------------------------------------------------ #
    # Public entry points
    # ------------------------------------------------------------------ #
    @staticmethod
    def load_from_folder(evidence_dir: str | Path) -> dict:
        """Build the engine payload from a `debate_evidence/<incident_id>/` folder.

        Preferred source of the six blocks is `incident_context.json` (the matching
        entry from `unified_master_dataset.json`). The enriched AMQP payload
        (`enriched_incident.json`) contributes the Phase 2 extras: fingerprint,
        correlation_id, similar_incidents and historical_context. Supporting files
        under `logs/`, `metrics/` and the top-level JSON files are folded in when
        present, without overriding canonical data.
        """
        evidence_dir = Path(evidence_dir)
        if not evidence_dir.is_dir():
            raise FileNotFoundError(f"Evidence folder not found: {evidence_dir}")

        payload: dict = {}

        # 1. Canonical six-block entry (primary source of truth).
        incident_context = EvidenceLoader._read_json(evidence_dir / "incident_context.json")
        if incident_context:
            payload.update({k: v for k, v in incident_context.items() if k in CANONICAL_BLOCKS})

        # 2. Enriched AMQP payload -> Phase 2 extras + fallback blocks.
        enriched = EvidenceLoader._read_json(evidence_dir / "enriched_incident.json")
        enriched_payload = (enriched or {}).get("payload", {}) if isinstance(enriched, dict) else {}
        EvidenceLoader._merge_enriched(payload, enriched, enriched_payload)

        # 3. Standalone block files (only fill gaps, never clobber).
        EvidenceLoader._fill_block_from_file(payload, "system_context", evidence_dir / "system_context.json")
        EvidenceLoader._fill_block_from_file(payload, "infrastructure_topology", evidence_dir / "topology.json")
        EvidenceLoader._fill_block_from_file(payload, "injected_chaos_context", evidence_dir / "chaos_context.json")

        # 4. Supporting raw evidence -> enrich telemetry_evidence.
        EvidenceLoader._merge_supporting_telemetry(payload, evidence_dir)

        # 5. Similar incidents / historical resolutions (Fact Checker material).
        similar = EvidenceLoader._read_json(evidence_dir / "similar_incidents.json")
        if similar is not None:
            payload["similar_incidents"] = similar
        elif enriched_payload.get("similar_incidents"):
            payload["similar_incidents"] = enriched_payload["similar_incidents"]

        # 6. Default instruction if none provided.
        payload.setdefault(
            "agent_instruction",
            "Analyze the provided telemetry evidence and dependency states. "
            "Determine the root cause and output a safe remediation plan.",
        )

        EvidenceLoader.normalize(payload)
        return payload

    @staticmethod
    def load_from_dataset(dataset_path: str | Path, incident_id: str | None = None) -> dict:
        """Extract one entry from `unified_master_dataset.json`.

        If `incident_id` is None, the hottest incident is used. The dataset contract
        guarantees `incidents[]` is pre-sorted descending by `priority_score`, so
        index 0 is the highest-priority case.
        """
        dataset_path = Path(dataset_path)
        data = EvidenceLoader._read_json(dataset_path)
        if not isinstance(data, dict):
            raise ValueError(f"Invalid dataset at {dataset_path}")

        incidents = data.get("incidents", [])
        if not incidents:
            raise ValueError(f"No incidents found in {dataset_path}")

        if incident_id is None:
            entry = incidents[0]  # hottest by contract
        else:
            match = next((i for i in incidents if i.get("incident_event", {}).get("incident_id") == incident_id), None)
            if match is None:
                raise KeyError(f"incident_id '{incident_id}' not found in dataset")
            entry = match

        payload = {k: v for k, v in entry.items() if k in CANONICAL_BLOCKS or k == "agent_instruction"}
        # Carry reproducibility metadata forward.
        metadata = data.get("metadata") or entry.get("metadata")
        if metadata:
            payload["metadata"] = metadata

        EvidenceLoader.normalize(payload)
        return payload

    @staticmethod
    def from_enriched_payload(enriched: dict) -> dict:
        """Build a payload directly from the `autosre.incident.enriched` AMQP message."""
        payload: dict = {}
        enriched_payload = enriched.get("payload", {}) if isinstance(enriched, dict) else {}
        EvidenceLoader._merge_enriched(payload, enriched, enriched_payload)
        payload.setdefault(
            "agent_instruction",
            "Analyze the provided telemetry evidence and dependency states. "
            "Determine the root cause and output a safe remediation plan.",
        )
        EvidenceLoader.normalize(payload)
        return payload

    # ------------------------------------------------------------------ #
    # Normalization & validation
    # ------------------------------------------------------------------ #
    @staticmethod
    def normalize(payload: dict) -> dict:
        """Apply the contract guarantees so downstream code can assume sane shapes."""
        telemetry = payload.setdefault("telemetry_evidence", {})

        # metrics_snapshot may legitimately be [] for fresh clusters -> keep as [].
        if telemetry.get("metrics_snapshot") is None:
            telemetry["metrics_snapshot"] = []
        if telemetry.get("log_samples") is None:
            telemetry["log_samples"] = []

        # log_cluster_template is guaranteed non-blank upstream; guard anyway.
        if not str(telemetry.get("log_cluster_template", "")).strip():
            telemetry["log_cluster_template"] = "NO_CLUSTER_TEMPLATE"

        # incident_event sanity.
        event = payload.setdefault("incident_event", {})
        event.setdefault("incident_id", "incident_0")
        event.setdefault("severity", "UNKNOWN")
        event.setdefault("priority_score", 0.0)
        event.setdefault("occurrence_count", 0)

        payload.setdefault("system_context", {})
        payload.setdefault("infrastructure_topology", {})
        payload.setdefault("service_health_status", {})
        payload.setdefault("injected_chaos_context", {})
        return payload

    @staticmethod
    def validate(payload: dict) -> list[str]:
        """Return a list of warnings (empty == clean). Non-fatal by design."""
        warnings: list[str] = []

        inc_id = payload.get("incident_event", {}).get("incident_id", "")
        if inc_id and not INCIDENT_ID_PATTERN.match(str(inc_id)):
            warnings.append(f"incident_id '{inc_id}' does not match ^[a-zA-Z0-9_-]+_\\d+$")

        telemetry = payload.get("telemetry_evidence", {})
        template = str(telemetry.get("log_cluster_template", ""))
        if not template.strip() or template.strip() == "NO_CLUSTER_TEMPLATE":
            warnings.append("log_cluster_template is blank/placeholder")
        if re.fullmatch(r"\s*at\s+<\*>\s*", template):
            warnings.append("log_cluster_template is a bare stack fragment ('at <*>')")

        if not telemetry.get("log_samples"):
            warnings.append("no log_samples present")
        if not telemetry.get("metrics_snapshot"):
            warnings.append("metrics_snapshot is empty (allowed for fresh clusters)")

        for block in CANONICAL_BLOCKS:
            if block not in payload:
                warnings.append(f"missing canonical block: {block}")

        return warnings

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _read_json(path: Path) -> Any:
        if not path.is_file():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _fill_block_from_file(payload: dict, block: str, path: Path) -> None:
        if block in payload and payload[block]:
            return
        data = EvidenceLoader._read_json(path)
        if data is not None:
            payload[block] = data

    @staticmethod
    def _merge_enriched(payload: dict, enriched: dict | None, enriched_payload: dict) -> None:
        """Fold the enriched AMQP message into the payload without clobbering blocks."""
        if not isinstance(enriched_payload, dict):
            enriched_payload = {}

        # Fill canonical blocks from the enriched payload only if still missing.
        for block in CANONICAL_BLOCKS:
            if block not in payload or not payload[block]:
                if block in enriched_payload and enriched_payload[block]:
                    payload[block] = enriched_payload[block]
        # topology / telemetry_evidence may be nested directly in the enriched payload.
        if "topology" in enriched_payload and not payload.get("infrastructure_topology"):
            payload["infrastructure_topology"] = enriched_payload["topology"]

        # Phase 2 correlation extras.
        if isinstance(enriched, dict):
            if enriched.get("correlation_id"):
                payload["correlation_id"] = enriched["correlation_id"]
            if enriched.get("incident_id") and not payload.get("incident_event", {}).get("incident_id"):
                payload.setdefault("incident_event", {})["incident_id"] = enriched["incident_id"]
        if enriched_payload.get("fingerprint"):
            payload["fingerprint"] = enriched_payload["fingerprint"]
        if enriched_payload.get("historical_context"):
            payload["historical_context"] = enriched_payload["historical_context"]
        if enriched_payload.get("similar_incidents"):
            payload["similar_incidents"] = enriched_payload["similar_incidents"]

    @staticmethod
    def _merge_supporting_telemetry(payload: dict, evidence_dir: Path) -> None:
        """Fold logs/ and metrics/ supporting files into telemetry_evidence (gap-fill only)."""
        telemetry = payload.setdefault("telemetry_evidence", {})

        # Drain3 cluster template.
        if not str(telemetry.get("log_cluster_template", "")).strip() or telemetry.get("log_cluster_template") == "NO_CLUSTER_TEMPLATE":
            cluster_txt = evidence_dir / "logs" / "cluster_template.txt"
            if cluster_txt.is_file():
                try:
                    telemetry["log_cluster_template"] = cluster_txt.read_text(encoding="utf-8").strip()
                except OSError:
                    pass

        # Log samples.
        if not telemetry.get("log_samples"):
            samples = EvidenceLoader._read_json(evidence_dir / "logs" / "samples.json")
            if isinstance(samples, list) and samples:
                telemetry["log_samples"] = samples

        # Metrics snapshot.
        if not telemetry.get("metrics_snapshot"):
            snapshot = EvidenceLoader._read_json(evidence_dir / "metrics" / "snapshot.json")
            if isinstance(snapshot, list) and snapshot:
                telemetry["metrics_snapshot"] = snapshot

        # Time series is supplementary trend data; attach without overriding snapshot.
        time_series = EvidenceLoader._read_json(evidence_dir / "metrics" / "time_series.json")
        if time_series is not None:
            telemetry["time_series"] = time_series
