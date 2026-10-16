"""Validator utilities for biosignal-mock responses.

The mock service returns newline‑delimited JSON objects with a strict schema.
This module checks that all mandatory top‑level keys are present, that types are
correct, and that numeric ranges make sense. It raises ``ValueError`` on any
validation failure so the collector can decide whether to retry, record a loss,
or abort.
"""

import json
from typing import Any, Dict, List

# Expected keys in every response (as per Section 4.1 of the assessment)
REQUIRED_KEYS = [
    "session_id",
    "sensor",
    "first_index",
    "count",
    "lost",
    "session_end",
    "crc32",
    "data",
]

def validate_response(response: Dict[str, Any]) -> None:
    """Validate a parsed JSON response from the mock.

    * All ``REQUIRED_KEYS`` must be present.
    * ``session_id`` – non‑empty string.
    * ``sensor`` – integer 1 or 2.
    * ``first_index`` – non‑negative integer.
    * ``count`` – positive integer matching length of ``data``.
    * ``lost`` – integer ≥ 0.
    * ``session_end`` – boolean.
    * ``crc32`` – 8‑character hex string or integer.
    * ``data`` – list of lists of three (sensor‑1) or two (sensor‑2) signed
      24‑bit integers.
    """
    missing = [k for k in REQUIRED_KEYS if k not in response]
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    # Basic type checks
    if not isinstance(response["session_id"], str) or not response["session_id"]:
        raise ValueError("session_id must be a non‑empty string")
    if response["sensor"] not in (1, 2):
        raise ValueError("sensor must be 1 or 2")
    if not isinstance(response["first_index"], int) or response["first_index"] < 0:
        raise ValueError("first_index must be a non‑negative integer")
    if not isinstance(response["count"], int) or response["count"] <= 0:
        raise ValueError("count must be a positive integer")
    if not isinstance(response["lost"], int) or response["lost"] < 0:
        raise ValueError("lost must be a non‑negative integer")
    if not isinstance(response["session_end"], bool):
        raise ValueError("session_end must be a boolean")
    # crc32 can be str or int – further CRC check is done by MockClient
    if not isinstance(response["data"], list):
        raise ValueError("data must be a list")
    if len(response["data"]) != response["count"]:
        raise ValueError("count does not match length of data array")

    # Verify each channel list length matches sensor spec
    expected_channels = 3 if response["sensor"] == 1 else 2
    for idx, row in enumerate(response["data"]):
        if not isinstance(row, list) or len(row) != expected_channels:
            raise ValueError(f"Row {idx} in data does not have {expected_channels} channels")
        for val in row:
            if not isinstance(val, int):
                raise ValueError(f"Channel value {val} is not an integer")
            # 24‑bit signed integer range check
            if val < -2**23 or val > 2**23 - 1:
                raise ValueError(f"Channel value {val} out of 24‑bit signed range")

    # If we reach here the response passes all structural checks.
    return None
