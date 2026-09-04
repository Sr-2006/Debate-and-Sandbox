#!/usr/bin/env python3
"""
Laptop 2 Always-Hot Supervisor Engine Service.

Continuously supervises:
  1. laptop2_incident_receiver.py
  2. laptop2_processing_worker.py

Features:
  - Automatic restart on unexpected process exit.
  - Heartbeat publication to autosre.system.laptop2.heartbeat.v1 every 5 seconds.
  - Health state monitoring: STARTING, IDLE, PROCESSING, DEGRADED.
  - Cross-incident persistence: remains alive indefinitely.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import nats
from nats.js.api import PubAck

# Ensure repository root is on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from shared.subjects import SUBJECT_LAPTOP2_HEARTBEAT
from shared.event_envelope import get_git_commit_sha
from transport.contracts import ProcessingStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Laptop2Supervisor] %(message)s"
)
logger = logging.getLogger("Laptop2Supervisor")

DEFAULT_NATS_URL = os.environ.get("AUTOSRE_NATS_URL", "nats://172.51.154.253:4222")
DEFAULT_STATE_DB = "runtime/transport.db"
DEFAULT_LOCK_FILE = "runtime/laptop2_engine_service.lock"
HEARTBEAT_INTERVAL_SECONDS = 5.0


class Laptop2EngineSupervisor:
    """Supervises Laptop 2 incident receiver and processing worker processes with heartbeat emission."""

    def __init__(
        self,
        nats_url: str = DEFAULT_NATS_URL,
        state_db_path: str = DEFAULT_STATE_DB,
        poll_interval: float = 2.0,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        receiver_timeout: float = 300.0,
        lock_file: str = DEFAULT_LOCK_FILE
    ):
        self.nats_url = nats_url
        self.state_db_path = state_db_path
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.receiver_timeout = receiver_timeout
        self.lock_file = os.path.abspath(lock_file)
        
        self.nc: Optional[nats.NATS] = None
        self.js = None
        self.receiver_proc: Optional[subprocess.Popen] = None
        self.worker_proc: Optional[subprocess.Popen] = None
        self.running = False
        self.lock_fp = None

    def acquire_lock(self) -> bool:
        """Ensures singleton execution of the supervisor service."""
        os.makedirs(os.path.dirname(self.lock_file), exist_ok=True)
        try:
            if sys.platform == "win32":
                import msvcrt
                self.lock_fp = open(self.lock_file, "w")
                msvcrt.locking(self.lock_fp.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                self.lock_fp = open(self.lock_file, "w")
                fcntl.flock(self.lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_fp.write(f"{os.getpid()}\n")
            self.lock_fp.flush()
            return True
        except Exception:
            return False

    def release_lock(self):
        """Releases file lock upon shutdown."""
        if self.lock_fp:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    self.lock_fp.seek(0)
                    msvcrt.locking(self.lock_fp.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.lock_fp.fileno(), fcntl.LOCK_UN)
                self.lock_fp.close()
            except Exception:
                pass
            try:
                if os.path.exists(self.lock_file):
                    os.remove(self.lock_file)
            except Exception:
                pass
            self.lock_fp = None

    def kill_existing_orphans(self):
        """Terminates any existing receiver or worker processes before spawning managed instances."""
        if sys.platform == "win32":
            try:
                cmd = (
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.CommandLine -match 'laptop2_incident_receiver|laptop2_processing_worker' "
                    f"-and $_.ProcessId -ne {os.getpid()} }} | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
                )
                subprocess.run(["powershell", "-Command", cmd], capture_output=True, timeout=5)
            except Exception as e:
                logger.warning(f"Could not scan orphan processes: {e}")

    def start_receiver(self):
        """Starts the laptop2_incident_receiver subprocess."""
        cmd = [
            sys.executable,
            os.path.join(REPO_ROOT, "scripts", "laptop2_incident_receiver.py"),
            "--nats-url", self.nats_url,
            "--timeout-seconds", str(self.receiver_timeout),
            "--state-db", self.state_db_path
        ]
        logger.info(f"Spawning Incident Receiver: {' '.join(cmd)}")
        self.receiver_proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    def start_worker(self):
        """Starts the laptop2_processing_worker subprocess."""
        cmd = [
            sys.executable,
            os.path.join(REPO_ROOT, "scripts", "laptop2_processing_worker.py"),
            "--nats-url", self.nats_url,
            "--poll-interval", str(self.poll_interval),
            "--db", self.state_db_path
        ]
        logger.info(f"Spawning Processing Worker: {' '.join(cmd)}")
        self.worker_proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    def check_and_supervise_subprocesses(self) -> Tuple[str, str]:
        """
        Checks health of child processes and restarts any that exited unexpectedly.
        Returns: (receiver_status, worker_status)
        """
        # 1. Receiver check
        if self.receiver_proc is None or self.receiver_proc.poll() is not None:
            exit_code = self.receiver_proc.poll() if self.receiver_proc else "None"
            logger.warning(f"Receiver subprocess not running (exit_code={exit_code}). Restarting...")
            self.start_receiver()
            receiver_status = "RESTARTING"
        else:
            receiver_status = "RUNNING"

        # 2. Worker check
        if self.worker_proc is None or self.worker_proc.poll() is not None:
            exit_code = self.worker_proc.poll() if self.worker_proc else "None"
            logger.warning(f"Worker subprocess not running (exit_code={exit_code}). Restarting...")
            self.start_worker()
            worker_status = "RESTARTING"
        else:
            worker_status = "RUNNING"

        return receiver_status, worker_status

    def get_database_processing_state(self) -> str:
        """Queries local state database to determine if worker is actively processing an incident."""
        if not os.path.exists(self.state_db_path):
            return "IDLE"
        try:
            conn = sqlite3.connect(self.state_db_path, timeout=1.0)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM incident_processing WHERE processing_status = 'PROCESSING';"
            )
            row = cursor.fetchone()
            count = row[0] if row else 0
            conn.close()
            return "PROCESSING" if count > 0 else "IDLE"
        except Exception:
            return "IDLE"

    def compute_supervisor_state(self, receiver_status: str, worker_status: str) -> str:
        """Determines aggregate system state: STARTING, IDLE, PROCESSING, DEGRADED."""
        if receiver_status != "RUNNING" or worker_status != "RUNNING":
            return "DEGRADED"
        db_state = self.get_database_processing_state()
        return "PROCESSING" if db_state == "PROCESSING" else "IDLE"

    async def connect_nats(self) -> bool:
        """Connects to NATS JetStream broker."""
        try:
            if self.nc and not self.nc.is_closed:
                return True
            self.nc = await nats.connect(self.nats_url, connect_timeout=3.0)
            self.js = self.nc.jetstream()
            logger.info(f"Connected to NATS broker at {self.nats_url}")
            return True
        except Exception as e:
            logger.warning(f"Could not connect to NATS broker: {e}")
            self.nc = None
            self.js = None
            return False

    async def publish_heartbeat(self, receiver_status: str, worker_status: str, state: str):
        """Publishes structured heartbeat message to autosre.system.laptop2.heartbeat.v1."""
        payload = {
            "engine": "laptop2",
            "receiver_status": receiver_status,
            "worker_status": worker_status,
            "state": state,
            "git_sha": get_git_commit_sha(),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        if not self.nc or self.nc.is_closed:
            await self.connect_nats()

        if self.js:
            try:
                payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
                await self.js.publish(SUBJECT_LAPTOP2_HEARTBEAT, payload_bytes, timeout=2.0)
                logger.debug(f"Published heartbeat: state={state}")
            except Exception as e:
                logger.warning(f"Failed publishing heartbeat: {e}")
                # Reset connection on error
                try:
                    await self.nc.close()
                except Exception:
                    pass
                self.nc = None
                self.js = None

    async def run(self):
        """Main supervisor loop."""
        if not self.acquire_lock():
            logger.error("Another instance of Laptop2EngineSupervisor is already running. Exiting.")
            return 1

        logger.info("Starting Laptop 2 Always-Hot Execution Node Supervisor...")
        self.running = True
        self.kill_existing_orphans()

        # Initial startup
        self.start_receiver()
        self.start_worker()

        # Connect NATS
        await self.connect_nats()

        last_heartbeat_time = 0.0

        try:
            while self.running:
                receiver_status, worker_status = self.check_and_supervise_subprocesses()
                current_state = self.compute_supervisor_state(receiver_status, worker_status)

                now = time.time()
                if now - last_heartbeat_time >= self.heartbeat_interval:
                    await self.publish_heartbeat(receiver_status, worker_status, current_state)
                    last_heartbeat_time = now

                await asyncio.sleep(1.0)

        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("Supervisor shutting down...")
        finally:
            self.shutdown()
        return 0

    def shutdown(self):
        """Gracefully terminates subprocesses and closes NATS connection."""
        self.running = False
        logger.info("Stopping supervised child processes...")
        for name, proc in [("Receiver", self.receiver_proc), ("Worker", self.worker_proc)]:
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        if self.nc and not self.nc.is_closed:
            try:
                asyncio.run(self.nc.close())
            except Exception:
                pass
        self.release_lock()
        logger.info("Supervisor shutdown complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="Laptop 2 Always-Hot Supervisor Service")
    parser.add_argument("--nats-url", default=DEFAULT_NATS_URL, help=f"NATS broker URL (default: {DEFAULT_NATS_URL})")
    parser.add_argument("--state-db", default=DEFAULT_STATE_DB, help=f"State database path (default: {DEFAULT_STATE_DB})")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Worker poll interval in seconds")
    parser.add_argument("--heartbeat-interval", type=float, default=5.0, help="Heartbeat interval in seconds")
    return parser.parse_args()


def main():
    args = parse_args()
    supervisor = Laptop2EngineSupervisor(
        nats_url=args.nats_url,
        state_db_path=args.state_db,
        poll_interval=args.poll_interval,
        heartbeat_interval=args.heartbeat_interval
    )

    def handle_signal(sig, frame):
        supervisor.running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    exit_code = asyncio.run(supervisor.run())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
