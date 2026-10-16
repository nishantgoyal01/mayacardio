"""Collector module for MayaCardio.

Continuously pulls data from the ``biosignal-mock`` TCP service, validates the
responses, stores samples durably, and records explicit loss ranges. It also
maintains a simple online/offline mode flag in the ``Storage`` instance based on
network health.

The collector runs in its own thread so the CLI can start it alongside the
synchronisation worker.
"""

import logging
import threading
import time
from typing import List, Tuple, Optional

from .mock_client import MockClient, MockClientError
from .validator import validate_response
from .storage import Storage

# Constants – tuned for the assessment requirements
BATCH_COUNT = 400            # request exactly one second of samples per batch
MAX_RETRIES = 5             # bounded retry attempts before switching to offline
RETRY_BASE_DELAY = 2.0      # seconds, exponential back‑off factor

class Collector(threading.Thread):
    """Background thread that collects sensor data from the mock service.

    It tracks per‑sensor ``next_index`` state, sends ``request`` calls, validates
    the response, inserts samples into :class:`Storage`, and records any loss
    reported by the mock. Network failures trigger a mode switch to ``offline``
    and exponential back‑off; a successful request after a failure restores the
    ``online`` mode.
    """

    def __init__(self, storage: Storage, host: str = "127.0.0.1", port: int = 9000):
        super().__init__(daemon=True)
        self.storage = storage
        self.client = MockClient(host=host, port=port)
        self._stop_event = threading.Event()
        # Per‑sensor next index to request. The mock starts both sensors together.
        self._next_index = {1: 0, 2: 0}
        self._session_id: Optional[str] = None
        self._mode = "online"
        logging.debug("Collector initialised for %s:%d", host, port)

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------
    def stop(self) -> None:
        """Signal the thread to terminate and close the socket."""
        self._stop_event.set()
        self.client.close()
        logging.info("Collector stop requested")

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------
    def run(self) -> None:
        """Thread entry point – keep requesting data for both sensors.

        The loop runs until ``stop()`` is called. Errors are handled per‑sensor
        with bounded retries and exponential back‑off. Mode changes are reflected
        in the shared ``Storage`` instance.
        """
        try:
            self.client.connect()
        except MockClientError as exc:
            logging.error("Initial connection failed: %s", exc)
            self._set_mode("offline")
        while not self._stop_event.is_set():
            for sensor in (1, 2):
                self._collect_sensor(sensor)
            # Tiny sleep to avoid a busy‑wait when both sensors are offline.
            time.sleep(0.01)
        logging.info("Collector thread exiting")

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    def _set_mode(self, mode: str) -> None:
        if mode != self._mode:
            self._mode = mode
            self.storage.set_mode(mode)
            logging.info("Mode switched to %s", mode)

    def _collect_sensor(self, sensor: int) -> None:
        """Request a batch for *sensor* and store the result.

        Handles retries, loss accounting, duplicate handling and session end.
        """
        retries = 0
        while retries <= MAX_RETRIES and not self._stop_event.is_set():
            try:
                # Use known session_id if available; the mock will echo it back.
                session_id = self._session_id or "unknown"
                resp = self.client.request(
                    session_id=session_id,
                    sensor=sensor,
                    first_index=self._next_index[sensor],
                    count=BATCH_COUNT,
                )
                # Validate structural correctness.
                validate_response(resp)

                # Capture real session_id on first successful response.
                if self._session_id is None:
                    self._session_id = resp["session_id"]
                    logging.debug("Captured session_id %s", self._session_id)

                # Record any loss reported by the mock.
                lost = resp.get("lost", 0)
                if lost:
                    start_lost = self._next_index[sensor]
                    end_lost = start_lost + lost - 1
                    self.storage.insert_loss(
                        session_id=resp["session_id"],
                        sensor=sensor,
                        start_idx=start_lost,
                        end_idx=end_lost,
                        reason="mock_reported_lost",
                    )
                    # Advance index past the lost range.
                    self._next_index[sensor] = end_lost + 1

                # Insert received samples.
                rows: List[Tuple[str, int, int, Optional[int], Optional[int], Optional[int]]] = []
                base_idx = resp["first_index"]
                for offset, channel_vals in enumerate(resp["data"]):
                    idx = base_idx + offset
                    ch1 = ch2 = ch3 = None
                    if sensor == 1:
                        ch1, ch2, ch3 = channel_vals  # type: ignore[misc]
                    else:
                        ch1, ch2 = channel_vals
                    rows.append((resp["session_id"], sensor, idx, ch1, ch2, ch3))
                self.storage.insert_samples(rows)

                # Update next_index for the next request.
                self._next_index[sensor] = resp["first_index"] + resp["count"]

                # Handle session termination.
                if resp.get("session_end"):
                    self.storage.insert_loss(
                        session_id=resp["session_id"],
                        sensor=sensor,
                        start_idx=self._next_index[sensor],
                        end_idx=None,
                        reason="session_end",
                    )
                    # Reset state – next request will start a new session.
                    self._session_id = None
                    self._next_index[sensor] = 0

                # Successful batch – ensure mode is online.
                self._set_mode("online")
                return  # batch completed successfully

            except MockClientError as exc:
                logging.warning("Sensor %d request failed (attempt %d): %s", sensor, retries + 1, exc)
                retries += 1
                self._set_mode("offline")
                # Exponential back‑off before retry.
                delay = RETRY_BASE_DELAY * (2 ** (retries - 1))
                time.sleep(min(delay, 30))
                # Re‑connect before next attempt.
                try:
                    self.client.connect()
                except MockClientError:
                    pass
        # Exhausted retries – log and move on; the next loop iteration will retry again.
        logging.error("Sensor %d gave up after %d retries – will retry later", sensor, MAX_RETRIES)
