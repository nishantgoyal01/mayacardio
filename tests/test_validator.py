import pytest
from mayacardio.validator import validate_response, MockClientError

# Valid sample payload (crc value is irrelevant for validator)
valid_payload = {
    "session_id": "sess1",
    "sensor": "ECG",
    "first_index": 0,
    "count": 2,
    "data": [
        {"index": 0, "ch1": 1000, "ch2": 2000, "ch3": 3000},
        {"index": 1, "ch1": 1100, "ch2": 2100, "ch3": 3100}
    ],
    "crc": 123456789
}

def test_validate_response_success():
    # Should not raise for a correct payload
    validate_response(valid_payload)

def test_validate_response_missing_field():
    incomplete = valid_payload.copy()
    incomplete.pop("sensor")
    with pytest.raises(MockClientError):
        validate_response(incomplete)

def test_validate_response_out_of_range():
    bad = valid_payload.copy()
    bad["data"][0]["ch1"] = -1  # negative value, out of allowed 0‑2^24‑1 range
    with pytest.raises(MockClientError):
        validate_response(bad)
