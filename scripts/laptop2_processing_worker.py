#!/usr/bin/env python3
"""CLI script for Laptop2 automated incident processing worker."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
import sys
import time


# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from transport.processing_worker import Laptop2ProcessingWorker
from transport.result_publisher import (
    DEFAULT_NATS_URL,
    DEFAULT_STREAM,
    DEFAULT_RESULT_SUBJECT,
    DEFAULT_STATE_DB
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Laptop 2 Automated Incident -> Pipeline -> Result Worker"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_STATE_DB,
        help=f"Path to SQLite transport database (default: {DEFAULT_STATE_DB})"
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
        help=f"Result publication subject (default: {DEFAULT_RESULT_SUBJECT})"
    )
    parser.add_argument(
        "--parent-event-id",
        default=None,
        help="Specific parent event_id to process"
    )
    parser.add_argument(
        "--pipeline-timeout-seconds",
        type=float,
        default=900.0,
        help="Timeout in seconds for pipeline subprocess execution (default: 900.0)"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process a single event and exit immediately"
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds to wait between polling cycles in service mode (default: 2.0)"
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Explicitly allow retrying previously FAILED processing attempts"
    )
    parser.add_argument(
        "--recover-stale",
        action="store_true",
        help="Explicitly recover stale PROCESSING claims left after worker crash"
    )
    return parser.parse_args()


async def main_async():
    args = parse_args()
    print("=" * 60)
    print("  LAPTOP 2 AUTOMATED REMEDIATION WORKER")
    print("=" * 60)
    print(f"  Transport DB   : {args.db}")
    print(f"  NATS URL       : {args.nats_url}")
    print(f"  Stream         : {args.stream}")
    print(f"  Result Subject : {args.subject}")
    print(f"  Target Event   : {args.parent_event_id or 'ANY_OLDEST_STAGED'}")
    print(f"  Mode           : {'ONCE' if args.once else 'CONTINUOUS_POLLING'}")
    print(f"  Retry Failed   : {args.retry_failed}")
    print(f"  Recover Stale  : {args.recover_stale}")
    print("=" * 60)

    worker = Laptop2ProcessingWorker(
        state_db_path=args.db,
        nats_url=args.nats_url,
        stream_name=args.stream,
        subject=args.subject,
        pipeline_timeout_seconds=args.pipeline_timeout_seconds
    )

    if args.once:
        res = await worker.process_event_async(
            parent_event_id=args.parent_event_id,
            retry_failed=args.retry_failed,
            recover_stale=args.recover_stale
        )
        print("\n[WORKER RUN SUMMARY]")
        print(json.dumps(res, indent=2))
        return 0 if res.get("status") in ["PROCESSING_COMPLETE", "ALREADY_COMPLETED", "SKIPPED_ALREADY_COMPLETED"] else 1

    print("Starting continuous worker polling loop (Ctrl+C to stop)...")
    try:
        while True:
            res = await worker.process_event_async(
                parent_event_id=args.parent_event_id,
                retry_failed=args.retry_failed,
                recover_stale=args.recover_stale
            )
            if res.get("status") not in ["NO_STAGED_EVENTS"]:
                print(f"[{datetime.now(timezone.utc).isoformat()}] {json.dumps(res)}")
            await asyncio.sleep(args.poll_interval)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nShutting down worker...")
    return 0


def main():
    exit_code = asyncio.run(main_async())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
