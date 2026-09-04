"""Unit and integration tests for Laptop 2 Always-Hot Supervisor Engine Service."""

import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from scripts.laptop2_engine_service import Laptop2EngineSupervisor
from shared.subjects import SUBJECT_LAPTOP2_HEARTBEAT


class TestLaptop2EngineSupervisor:
    """Validates the Always-Hot Supervisor, health state machine, and heartbeat emission."""

    def test_singleton_lock_acquisition_and_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = os.path.join(temp_dir, "test_service.lock")
            sup1 = Laptop2EngineSupervisor(lock_file=lock_path)
            sup2 = Laptop2EngineSupervisor(lock_file=lock_path)

            assert sup1.acquire_lock() is True
            # Second supervisor should fail to acquire lock
            assert sup2.acquire_lock() is False

            sup1.release_lock()
            # After release, sup2 can acquire lock
            assert sup2.acquire_lock() is True
            sup2.release_lock()

    def test_state_computation_idle_processing_degraded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "transport.db")
            sup = Laptop2EngineSupervisor(state_db_path=db_path)

            # Both running, no active incident -> IDLE
            assert sup.compute_supervisor_state("RUNNING", "RUNNING") == "IDLE"

            # Receiver degraded -> DEGRADED
            assert sup.compute_supervisor_state("RESTARTING", "RUNNING") == "DEGRADED"

            # Worker degraded -> DEGRADED
            assert sup.compute_supervisor_state("RUNNING", "STOPPED") == "DEGRADED"

            # When database has processing_status = 'PROCESSING' -> PROCESSING
            with patch.object(sup, "get_database_processing_state", return_value="PROCESSING"):
                assert sup.compute_supervisor_state("RUNNING", "RUNNING") == "PROCESSING"

    def test_heartbeat_payload_format(self):
        async def _run():
            sup = Laptop2EngineSupervisor()
            mock_nc = MagicMock()
            mock_nc.is_closed = False
            mock_js = AsyncMock()

            sup.nc = mock_nc
            sup.js = mock_js

            published_payloads = []

            async def mock_publish(subject, payload_bytes, timeout=2.0):
                data = json.loads(payload_bytes.decode("utf-8"))
                published_payloads.append((subject, data))
                return MagicMock()

            mock_js.publish = AsyncMock(side_effect=mock_publish)

            await sup.publish_heartbeat(
                receiver_status="RUNNING",
                worker_status="RUNNING",
                state="IDLE"
            )

            assert len(published_payloads) == 1
            subject, payload = published_payloads[0]

            assert subject == SUBJECT_LAPTOP2_HEARTBEAT
            assert payload["engine"] == "laptop2"
            assert payload["receiver_status"] == "RUNNING"
            assert payload["worker_status"] == "RUNNING"
            assert payload["state"] == "IDLE"
            assert "git_sha" in payload
            assert "timestamp" in payload

        asyncio.run(_run())

    def test_subprocess_supervision_restart_trigger(self):
        sup = Laptop2EngineSupervisor()

        # Mock dead receiver process (poll returns exit code 1)
        mock_dead_receiver = MagicMock()
        mock_dead_receiver.poll.return_value = 1

        mock_alive_worker = MagicMock()
        mock_alive_worker.poll.return_value = None

        sup.receiver_proc = mock_dead_receiver
        sup.worker_proc = mock_alive_worker

        with patch.object(sup, "start_receiver") as mock_start_recv:
            r_status, w_status = sup.check_and_supervise_subprocesses()
            assert r_status == "RESTARTING"
            assert w_status == "RUNNING"
            mock_start_recv.assert_called_once()
