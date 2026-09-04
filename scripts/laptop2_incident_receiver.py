#!/usr/bin/env python3
"""CLI script to run the Laptop2 NATS JetStream incident receiver."""

import argparse
import asyncio
import json
import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from transport.nats_receiver import (
    Laptop2IncidentReceiver,
    DEFAULT_NATS_URL,
    DEFAULT_SUBJECT,
    DEFAULT_STATE_DB,
    DEFAULT_INPUT_DIR
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Laptop 2 Cross-Laptop Incident Event Receiver"
    )
    parser.add_argument(
        "--nats-url",
        default=DEFAULT_NATS_URL,
        help=f"NATS JetStream URL (default: {DEFAULT_NATS_URL})"
    )
    parser.add_argument(
        "--stream",
        default=os.environ.get("AUTOSRE_STREAM", "AUTOSRE"),
        help="JetStream stream name (default: AUTOSRE)"
    )
    parser.add_argument(
        "--subject",
        default=DEFAULT_SUBJECT,
        help=f"Subject to listen for incident events (default: {DEFAULT_SUBJECT})"
    )
    parser.add_argument(
        "--state-db",
        default=DEFAULT_STATE_DB,
        help=f"Path to SQLite deduplication database (default: {DEFAULT_STATE_DB})"
    )
    parser.add_argument(
        "--input-dir",
        default=DEFAULT_INPUT_DIR,
        help=f"Directory to stage incoming case JSON files (default: {DEFAULT_INPUT_DIR})"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process exactly one message and exit"
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Timeout in seconds when waiting for messages (default: 30.0)"
    )
    return parser.parse_args()


async def main_async():
    args = parse_args()
    print("=" * 60)
    print("  LAPTOP 2 NATS JETSTREAM INCIDENT RECEIVER")
    print("=" * 60)
    print(f"  NATS URL     : {args.nats_url}")
    print(f"  Stream       : {args.stream}")
    print(f"  Subject      : {args.subject}")
    print(f"  State DB     : {args.state_db}")
    print(f"  Input Staging: {args.input_dir}")
    print(f"  Mode         : {'ONCE' if args.once else 'CONTINUOUS'}")
    print("=" * 60)

    receiver = Laptop2IncidentReceiver(
        nats_url=args.nats_url,
        stream_name=args.stream,
        subject=args.subject,
        state_db_path=args.state_db,
        input_dir=args.input_dir
    )

    if args.once:
        print(f"Waiting up to {args.timeout_seconds}s for a single incident message...")
        try:
            summary = await receiver.process_single_message(timeout=args.timeout_seconds)
            if summary:
                print("\n[RECEIVER SUMMARY]")
                print(json.dumps(summary, indent=2))
                return 0 if summary.get("status") in ["STAGED", "ALREADY_STAGED", "SEMANTIC_DUPLICATE_STAGED"] else 1
            else:
                print("\n[TIMEOUT] No incident message received within timeout period.")
                return 2
        except Exception as e:
            print(f"\n[ERROR] Receiver encountered exception: {e}")
            return 1
        finally:
            await receiver.close()
    else:
        print("Starting continuous receiver loop (Ctrl+C to stop)...")
        try:
            while True:
                summary = await receiver.process_single_message(timeout=5.0)
                if summary:
                    print(f"[{summary.get('processed_at')}] Event: {summary.get('event_id')} -> Status: {summary.get('status')}")
                await asyncio.sleep(0.1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nShutting down receiver...")
        finally:
            await receiver.close()
        return 0


def main():
    exit_code = asyncio.run(main_async())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
