"""Storage layer for MayaCardio.

Provides durable SQLite persistence with WAL mode, full sync, and explicit loss
recording. All writes are wrapped in a thread‑safe transaction and duplicate
samples are ignored via ``INSERT OR IGNORE``. The ``sync_state`` table tracks
whether a sample has been successfully uploaded to the cloud.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
import logging
from typing import List, Tuple, Optional

DB_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;

CREATE TABLE IF NOT EXISTS samples (
    session_id TEXT NOT NULL,
    sensor INTEGER NOT NULL,
    sample_index INTEGER NOT NULL,
    ch1 INTEGER,
    ch2 INTEGER,
    ch3 INTEGER,
    PRIMARY KEY (session_id, sensor, sample_index)
);

CREATE TABLE IF NOT EXISTS losses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    sensor INTEGER NOT NULL,
    start_index INTEGER NOT NULL,
    end_index INTEGER,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    session_id TEXT NOT NULL,
    sensor INTEGER NOT NULL,
    sample_index INTEGER NOT NULL,
    synced INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, sensor, sample_index)
);
"""

class Storage:
    """Durable SQLite storage with WAL and ``synchronous=FULL``.

    * Inserts are idempotent – duplicate samples are ignored.
    * Losses are recorded in a dedicated table.
    * ``sync_state`` tracks which rows have been successfully uploaded.
    * Thread‑safe via a ``threading.Lock`` protecting each transaction.
    """

    def __init__(self, db_path: str = "mayacardio.db"):
        self.db_path = db_path
        # ``check_same_thread=False`` allows usage from multiple worker threads.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=FULL;")
        self._init_schema()
        self._lock = threading.Lock()
        self._mode = "online"  # placeholder, will be updated by collector
        logging.debug("Storage initialised – DB: %s", self.db_path)

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(DB_SCHEMA)
        logging.debug("Database schema ensured")

    @contextmanager
    def _transaction(self):
        """Execute a block inside a single transaction with exclusive lock.
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN")
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    # ---------------------------------------------------------------------
    # Sample handling
    # ---------------------------------------------------------------------
    def insert_samples(self, rows: List[Tuple[str, int, int, Optional[int], Optional[int], Optional[int]]]) -> None:
        """Insert many sample rows.

        ``rows`` is a list of ``(session_id, sensor, sample_index, ch1, ch2, ch3)``.
        Duplicates are ignored (``INSERT OR IGNORE``). For each inserted sample a
        corresponding entry is created in ``sync_state`` with ``synced = 0``.
        """
        with self._transaction() as cur:
            cur.executemany(
                "INSERT OR IGNORE INTO samples (session_id, sensor, sample_index, ch1, ch2, ch3) VALUES (?,?,?,?,?,?)",
                rows,
            )
            cur.executemany(
                "INSERT OR IGNORE INTO sync_state (session_id, sensor, sample_index, synced) VALUES (?,?,?,0)",
                [(r[0], r[1], r[2]) for r in rows],
            )
        logging.debug("Inserted %d sample rows", len(rows))

    # ---------------------------------------------------------------------
    # Loss handling
    # ---------------------------------------------------------------------
    def insert_loss(self, session_id: str, sensor: int, start_idx: int, end_idx: Optional[int], reason: str) -> None:
        """Record an explicit loss range.

        ``end_idx`` may be ``None`` when the upper bound is unknown.
        """
        with self._transaction() as cur:
            cur.execute(
                "INSERT INTO losses (session_id, sensor, start_index, end_index, reason) VALUES (?,?,?,?,?)",
                (session_id, sensor, start_idx, end_idx, reason),
            )
        logging.info(
            "Loss recorded – session %s sensor %d [%s-%s] reason=%s",
            session_id,
            sensor,
            start_idx,
            end_idx if end_idx is not None else "?",
            reason,
        )

    # ---------------------------------------------------------------------
    # Sync state helpers
    # ---------------------------------------------------------------------
    def get_unsynced_batch(self, limit: int = 2000) -> List[Tuple]:
        """Return a batch of unsynced samples.

        The caller receives the raw columns needed for the cloud payload.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT s.session_id, s.sensor, s.sample_index, s.ch1, s.ch2, s.ch3 "
                "FROM samples s JOIN sync_state ss "
                "ON s.session_id = ss.session_id AND s.sensor = ss.sensor AND s.sample_index = ss.sample_index "
                "WHERE ss.synced = 0 LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
        logging.debug("Fetched %d unsynced rows (limit %d)", len(rows), limit)
        return rows

    def mark_synced(self, keys: List[Tuple[str, int, int]]) -> None:
        """Mark the supplied ``(session_id, sensor, sample_index)`` rows as synced.
        """
        with self._transaction() as cur:
            cur.executemany(
                "UPDATE sync_state SET synced = 1 WHERE session_id = ? AND sensor = ? AND sample_index = ?",
                keys,
            )
        logging.debug("Marked %d rows as synced", len(keys))

    # ---------------------------------------------------------------------
    # Misc queries used by CLI
    # ---------------------------------------------------------------------
    def count_samples(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM samples")
        return cur.fetchone()[0]

    def count_synced_samples(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM sync_state WHERE synced = 1")
        return cur.fetchone()[0]

    def count_unsynced(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM sync_state WHERE synced = 0")
        return cur.fetchone()[0]

    # ---------------------------------------------------------------------
    # Mode handling – simple getter/setter used by collector & CLI
    # ---------------------------------------------------------------------
    def current_mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        logging.info("Device mode set to %s", mode)

    # ---------------------------------------------------------------------
    # Export helpers used by CLI ``pipeline export``
    # ---------------------------------------------------------------------
    def iter_samples(self, session_id: str):
        cur = self._conn.execute(
            "SELECT sensor, sample_index, ch1, ch2, ch3 FROM samples WHERE session_id = ? ORDER BY sensor, sample_index",
            (session_id,),
        )
        for sensor, idx, c1, c2, c3 in cur:
            values = [v for v in (c1, c2, c3) if v is not None]
            yield {"sensor": sensor, "index": idx, "values": values}

    def iter_losses(self, session_id: str):
        cur = self._conn.execute(
            "SELECT sensor, start_index, end_index, reason FROM losses WHERE session_id = ? ORDER BY sensor, start_index",
            (session_id,),
        )
        for sensor, start, end, reason in cur:
            yield {"sensor": sensor, "from_index": start, "to_index": end, "reason": reason}

