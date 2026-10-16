'''Mock client for the MayaCardio collector.

The original project expected a TCP client that talked to a separate
``biosignal‑mock`` service. For the purpose of a self‑contained demo we
replace it with an in‑process stub that generates deterministic fake
samples. The stub implements the minimal public API used by
`collector.py`:

* ``connect()`` – establish a (pretend) connection.
* ``close()`` – close the connection.
* ``request(session_id: str, sensor: int, first_index: int, count: int)`` –
  return a dictionary that mimics the real service response.

The response schema matches what ``collector.py`` validates:
```
{
    "session_id": str,
    "sensor": int,
    "first_index": int,
    "count": int,
    "data": List[Tuple[int, ...]],  # length 3 for sensor 1, 2 for sensor 2
    "lost": int,                    # number of lost samples (here always 0)
    "session_end": bool,            # optional, false for the demo
}
```

This implementation is deliberately simple – it does not perform any
network I/O, so it works inside the sandbox and requires no external
dependencies.
''' 

import uuid
import random
from typing import List, Tuple, Dict


class MockClientError(RuntimeError):
    """Raised when the mock client encounters a simulated error.

    The collector catches this exception to trigger its retry/back‑off
    logic. In the demo we never raise it, but the class is kept for API
    compatibility.
    """


class MockClient:
    """A lightweight stand‑in for the real TCP mock client.

    Parameters
    ----------
    host: str
        Ignored – kept for signature compatibility.
    port: int
        Ignored – kept for signature compatibility.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9000) -> None:
        self.host = host
        self.port = port
        self._connected = False
        # Use a deterministic seed so repeated runs produce the same data.
        random.seed(0)

    # ---------------------------------------------------------------------
    # Connection handling (no‑op for the stub)
    # ---------------------------------------------------------------------
    def connect(self) -> None:
        """Pretend to open a TCP connection.
        
        The real client would raise ``MockClientError`` on failure – the
        stub simply marks itself as connected.
        """
        self._connected = True

    def close(self) -> None:
        """Close the (pretend) connection."""
        self._connected = False

    # ---------------------------------------------------------------------
    # Request generation
    # ---------------------------------------------------------------------
    def request(
        self,
        *,
        session_id: str,
        sensor: int,
        first_index: int,
        count: int,
    ) -> Dict:
        """Generate a fake batch of samples.

        The method returns a dictionary that mirrors the structure of the
        real ``biosignal‑mock`` service. ``session_id`` is propagated; if the
        caller passes ``"unknown"`` we generate a fresh UUID.
        """
        if not self._connected:
            raise MockClientError("Client is not connected")

        # Ensure a valid session identifier.
        if session_id == "unknown":
            session_id = str(uuid.uuid4())

        # Build ``data`` – a list of tuples of channel values.
        data: List[Tuple[int, ...]] = []
        for _ in range(count):
            if sensor == 1:
                # Three channels for sensor 1.
                data.append([
                    random.randint(0, 1023),
                    random.randint(0, 1023),
                    random.randint(0, 1023),
                ])
            else:
                # Two channels for sensor 2.
                data.append([
                    random.randint(0, 1023),
                    random.randint(0, 1023),
                ])

        # The stub never loses samples and never ends a session.
        response: Dict = {
            "session_id": session_id,
            "sensor": sensor,
            "first_index": first_index,
            "count": count,
            "data": data,
            "lost": 0,
            "session_end": False,
            "crc32": 0,
        }
        return response

    # ---------------------------------------------------------------------
    # Representation helpers (optional, aid debugging)
    # ---------------------------------------------------------------------
    def __repr__(self) -> str:
        return f"<MockClient host={self.host!r} port={self.port!r} connected={self._connected}>"
