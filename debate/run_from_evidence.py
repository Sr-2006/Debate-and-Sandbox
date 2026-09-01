"""Phase 3 entry point: run the debate engine from a `debate_evidence/<incident_id>/`
folder (or a `unified_master_dataset.json` entry) and publish the conclusion as
`autosre.action.proposed` for Phase 4.

Usage:
    # From an assembled evidence folder
    python run_from_evidence.py --folder debate_evidence/payment-service_14

    # From the synced master dataset (hottest incident by default)
    python run_from_evidence.py --dataset unified_master_dataset.json
    python run_from_evidence.py --dataset unified_master_dataset.json --incident api-gateway_2

    # Publish to RabbitMQ (falls back to file if broker/pika unavailable)
    python run_from_evidence.py --folder debate_evidence/payment-service_14 --publish
"""

import argparse
import asyncio
import json

from debate_manager import DebateManager
from evidence_loader import EvidenceLoader
from action_publisher import ActionPublisher, build_action_proposed


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run debate engine from Phase 1/2 evidence.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--folder", help="Path to debate_evidence/<incident_id>/ folder")
    group.add_argument("--dataset", help="Path to unified_master_dataset.json")
    parser.add_argument("--incident", help="incident_id to select from the dataset (default: hottest)")
    parser.add_argument("--publish", action="store_true", help="Publish autosre.action.proposed for Phase 4")
    parser.add_argument("--offline-dir", help="Directory for offline action output (default: output/proposed_actions)")
    args = parser.parse_args()

    # 1. Load + normalize evidence into the engine payload contract.
    if args.folder:
        payload = EvidenceLoader.load_from_folder(args.folder)
    else:
        payload = EvidenceLoader.load_from_dataset(args.dataset, args.incident)

    warnings = EvidenceLoader.validate(payload)
    if warnings:
        print("[EvidenceLoader] Warnings:")
        for w in warnings:
            print(f"  - {w}")

    incident_id = payload.get("incident_event", {}).get("incident_id", "incident_0")
    print(f"[EvidenceLoader] Loaded incident: {incident_id}")

    # 2. Run the debate.
    manager = DebateManager()
    result = await manager.run_async(payload)

    print("\n" + "=" * 60)
    print(f"          DEBATE RESULT ({incident_id})")
    print("=" * 60)
    print(f"Execution Tier:   {result.get('execution_tier')}")
    print(f"Confidence:       {result.get('confidence_score')}%")
    print(f"Safety Veto:      {result.get('safety_violation')}")
    print(f"Total Latency:    {result.get('total_latency_seconds')}s")
    print("=" * 60)

    # 3. Build + publish the Phase 4 action envelope.
    message = build_action_proposed(
        incident_id=incident_id,
        result=result,
        correlation_id=payload.get("correlation_id"),
        fingerprint=payload.get("fingerprint"),
    )

    if args.publish:
        publisher = ActionPublisher()
        status = publisher.publish(message, offline_dir=args.offline_dir)
        print(f"[Publisher] transport={status['transport']} ok={status['ok']} detail={status['detail']}")
    else:
        print("[Publisher] --publish not set; action envelope preview:")
        print(json.dumps(message, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
