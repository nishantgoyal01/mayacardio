import pytest
from mayacardio.mock_client import MockClient, MockClientError

def test_mock_client_connection(monkeypatch):
    # Use monkeypatch to simulate socket connection failure
    monkeypatch.setattr('socket.socket', lambda *args, **kwargs: None)
    with pytest.raises(MockClientError):
        client = MockClient(host='localhost', port=9999)
        client.connect()
