#!/usr/bin/env python3
"""CLI script to publish Laptop2 Phase 3/4 remediation result event to NATS JetStream."""

import argparse
import asyncio
import glob
import json
import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from transport.dedup_store import DedupStore
from transport.result_publisher import (
    build_phase34_completed_event,
    Laptop2ResultPublisher,
    DEFAULT_NATS_URL,
    DEFAULT_STREAM,
    DEFAULT_RESULT_SUBJECT,
    DEFAULT_STATE_DB,
)


def find_latest_report(incident_id: str = None) -> str:
    """Finds the latest generated phase34_report.json file."""
    pattern = f"reports/verify_*/cases/{incident_id or '*'}/phase34_report.json"
    files = glob.glob(pattern)
    if not files:
        raise FileNotFoundError(f"No reports found matching pattern: {pattern}")
    files.sort(key=os.path.getmtime)
    return files[-1]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Laptop 2 Remediation Result Event Publisher"
    )
    parser.add_argument(
        "--incident-id",
        default=None,
        help="Incident ID to publish result for (e.g. order-service_51)"
    )
    parser.add_argument(
        "--report-path",
        default=None,
        help="Path to phase34_report.json (defaults to latest generated report for incident)"
    )
    parser.add_argument(
        "--parent-event-id",
        default=None,
        help="Inbound incident event_id (if omitted, looked up from transport.db)"
    )
    parser.add_argument(
        "--correlation-id",
        default=None,
        help="Correlation ID (if omitted, looked up from transport.db)"
    )
    parser.add_argument(
        "--nats-url",
        default=DEFAULT_NATS_URL,
        help=f"NATS JetStream URL (default: {DEFAULT_NATS_URL})"
    )
    parser.add_argument(
        "--stream",
        default=DEFAULT_STREAM,
        help=f"JetStream stream name (default: {DEFAULT_STREAM})"
    )
    parser.add_argument(
        "--subject",
        default=DEFAULT_RESULT_SUBJECT,
        help=f"Subject to publish result to (default: {DEFAULT_RESULT_SUBJECT})"
    )
    parser.add_argument(
        "--state-db",
        default=DEFAULT_STATE_DB,
        help=f"Path to SQLite deduplication database (default: {DEFAULT_STATE_DB})"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate result event without publishing to NATS"
    )
    return parser.parse_args()


async def main_async():
    args = parse_args()
    dedup = DedupStore(args.state_db)

    incident_id = args.incident_id
    parent_event_id = args.parent_event_id
    correlation_id = args.correlation_id
    input_payload_sha256 = None

    # If parent_event_id is given, resolve directly by event_id for maximum precision
    if parent_event_id:
        parent_row = dedup.get_event(parent_event_id)
        if parent_row:
            correlation_id = correlation_id or parent_row["correlation_id"]
            incident_id = incident_id or parent_row["incident_id"]
            input_payload_sha256 = parent_row["payload_hash"]

    # If incident_id not specified, check DB for latest staged event
    if not incident_id and not args.report_path:
        with dedup._connection() as conn:
            cursor = conn.execute(
                "SELECT event_id, correlation_id, incident_id, payload_hash FROM received_events ORDER BY updated_at DESC LIMIT 1;"
            )
            row = cursor.fetchone()
            if row:
                incident_id = row["incident_id"]
                if not parent_event_id:
                    parent_event_id = row["event_id"]
                if not correlation_id:
                    correlation_id = row["correlation_id"]
                if not input_payload_sha256:
                    input_payload_sha256 = row["payload_hash"]

    report_path = args.report_path or find_latest_report(incident_id)
    if not os.path.exists(report_path):
        print(f"[ERROR] Report file not found: {report_path}", file=sys.stderr)
        return 1

    with open(report_path, "r", encoding="utf-8") as f:
        report_data = json.load(f)

    if not incident_id:
        incident_id = (
            report_data.get("problem", {}).get("case_id")
            or report_data.get("incident_id")
            or "unknown_incident"
        )

    # If parent/correlation/input hash not provided, query transport.db for the inbound event matching this incident
    if not parent_event_id or not correlation_id or not input_payload_sha256:
        with dedup._connection() as conn:
            cursor = conn.execute(
                """
                SELECT event_id, correlation_id, payload_hash FROM received_events
                WHERE incident_id = ?
                ORDER BY updated_at DESC LIMIT 1;
                """,
                (incident_id,)
            )
            row = cursor.fetchone()
            if row:
                if not parent_event_id:
                    parent_event_id = row["event_id"]
                if not correlation_id:
                    correlation_id = row["correlation_id"]
                if not input_payload_sha256:
                    input_payload_sha256 = row["payload_hash"]

    if not parent_event_id:
        print("[ERROR] parent_event_id could not be resolved from arguments or transport.db", file=sys.stderr)
        return 1
    if not correlation_id:
        print("[ERROR] correlation_id could not be resolved from arguments or transport.db", file=sys.stderr)
        return 1
    if not input_payload_sha256:
        print("[ERROR] input_payload_sha256 could not be resolved from transport.db", file=sys.stderr)
        return 1

    event = build_phase34_completed_event(
        report=report_data,
        parent_event_id=parent_event_id,
        correlation_id=correlation_id,
        input_payload_sha256=input_payload_sha256,
        report_path=report_path
    )

    print("=" * 60)
    print("  LAPTOP 2 REMEDIATION RESULT PUBLISHER")
    print("=" * 60)
    print(f"  Report Path    : {report_path}")
    print(f"  Incident ID    : {incident_id}")
    print(f"  Parent Event ID: {parent_event_id}")
    print(f"  Correlation ID : {correlation_id}")
    print(f"  Result Event ID: {event['event_id']}")
    print(f"  Final Outcome  : {event['final_outcome']}")
    print(f"  NATS URL       : {args.nats_url}")
    print(f"  Subject        : {args.subject}")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN] Event payload validated successfully:")
        print(json.dumps(event, indent=2))
        return 0

    publisher = Laptop2ResultPublisher(
        nats_url=args.nats_url,
        stream_name=args.stream,
        subject=args.subject,
        state_db_path=args.state_db
    )

    try:
        res = await publisher.publish_result(event, report_path=report_path)
        print("\n[PUBLISHER SUMMARY]")
        print(json.dumps(res, indent=2))
        return 0
    except Exception as e:
        print(f"\n[ERROR] Failed to publish result event: {e}", file=sys.stderr)
        return 1
    finally:
        await publisher.close()


def main():
    exit_code = asyncio.run(main_async())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
