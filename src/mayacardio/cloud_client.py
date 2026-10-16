"""Cloud client for uploading batches to the FastAPI ingestion service.

The client uses ``httpx`` for async‑compatible HTTP calls (but operates in a
blocking fashion for simplicity). It serialises a list of sample dictionaries
into JSON and POSTs to ``/ingest``. On any non‑2xx response or network error it
raises ``CloudUploadError`` so the ``SyncWorker`` can retry.
"""

import json
import logging
import os
from typing import List, Dict, Any

import httpx

class CloudUploadError(RuntimeError):
    """Raised when an upload to the cloud fails or returns an unexpected status."""

class CloudClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self.session = httpx.Client(timeout=10.0)
        self.logger = logging.getLogger(__name__)
        self.logger.debug("CloudClient initialised for %s", self.base_url)

    def upload_batch(self, payload: List[Dict[str, Any]]) -> None:
        """Upload a batch of samples.

        ``payload`` is a list of dicts with keys ``session_id``, ``sensor``,
        ``index`` and ``values`` (list of channel integers). The method POSTs to
        ``{base_url}/ingest`` and raises ``CloudUploadError`` on failure.
        """
        url = f"{self.base_url}/ingest"
        try:
            response = self.session.post(url, json=payload)
        except httpx.RequestError as exc:
            raise CloudUploadError(f"Network error while uploading: {exc}") from exc
        if response.status_code != 200:
            raise CloudUploadError(
                f"Unexpected status {response.status_code}: {response.text}"
            )
        # Expect JSON response with {'ok': True}
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise CloudUploadError("Invalid JSON response from cloud") from exc
        if not data.get("ok"):
            raise CloudUploadError(f"Cloud reported failure: {data}")
        self.logger.debug("Successfully uploaded batch of %d samples", len(payload))
