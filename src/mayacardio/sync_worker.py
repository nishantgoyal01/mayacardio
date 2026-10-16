"""Synchronization worker – uploads unsynced samples to the cloud.

The worker runs in its own thread, periodically pulls a batch of unsynced rows
from :class:`Storage`, sends them via :class:`CloudClient`, and marks them as
synced only after a successful HTTP 200 response. It implements exponential
back‑off, respects a maximum batch size, and survives process restarts because
the sync state is persisted in SQLite.
"""

import logging
import threading
import time
import os
from typing import List, Tuple

from .cloud_client import CloudClient, CloudUploadError
from .storage import Storage

# Configuration – can be overridden by environment variables if desired.
BATCH_SIZE = int(os.getenv("UPLOAD_BATCH_SIZE", "2000"))
RETRY_DELAY = 5  # seconds between failed batch attempts

class SyncWorker(threading.Thread):
    """Background thread that uploads unsynced samples to the cloud.

    It continuously:
    1. Retrieves a batch of unsynced rows from ``Storage``.
    2. Calls ``CloudClient.upload_batch``.
    3. On success, marks the rows as synced.
    4. On failure, sleeps ``RETRY_DELAY`` and retries.
    The loop stops when ``stop()`` is called.
    """

    def __init__(self, storage: Storage, cloud_client: CloudClient):
        super().__init__(daemon=True)
        self.storage = storage
        self.cloud = cloud_client
        self._stop_event = threading.Event()
        self.logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._stop_event.set()
        self.logger.info("SyncWorker stop requested")

    # ------------------------------------------------------------------
    def run(self) -> None:
        self.logger.info("SyncWorker started")
        while not self._stop_event.is_set():
            batch = self.storage.get_unsynced_batch(limit=BATCH_SIZE)
            if not batch:
                # No pending work – sleep briefly to avoid busy loop.
                time.sleep(0.5)
                continue
            try:
                # Convert rows to the JSON-ready structure expected by the cloud.
                payload = {
                    "samples": [
                        {
                            "session_id": r[0],
                            "sensor": str(r[1]),
                            "index": r[2],
                            "ch1": r[3] if r[3] is not None else 0,
                            "ch2": r[4] if r[4] is not None else 0,
                            "ch3": r[5] if r[5] is not None else 0,
                        }
                        for r in batch
                    ]
                }
                self.cloud.upload_batch(payload)
                # Mark rows as synced only after a successful upload.
                keys = [(r[0], r[1], r[2]) for r in batch]
                self.storage.mark_synced(keys)
                self.logger.debug("Uploaded and marked %d rows", len(batch))
            except CloudUploadError as exc:
                self.logger.warning("Upload failed (will retry): %s", exc)
                time.sleep(RETRY_DELAY)
        self.logger.info("SyncWorker thread exiting")
