# Design Note – MayaCardio System

## 1. Architecture Overview
The MayaCardio platform consists of two logical subsystems:
1. **Device side (Raspberry‑Pi class Linux)** – collects sensor streams from the `biosignal‑mock` TCP service, stores every sample locally in SQLite, processes data with deterministic analytics, and synchronises unsent rows to a cloud service.
2. **Cloud side (FastAPI + PostgreSQL)** – provides a stateless `/ingest` endpoint that accepts batches of samples, stores them idempotently, and can optionally expose the same deterministic analytics.

Both subsystems share the **exact same processing code** (`processor.py`) ensuring identical results irrespective of where the computation runs.

## 2. Data‑Integrity Guarantees
- **SQLite durability** – WAL journal mode with `PRAGMA synchronous=FULL`. Each batch of 400 samples is written in a single transaction; on a sudden power loss the maximum possible loss is the *last incomplete transaction* (≤ 400 samples).
- **Loss accounting** – Whenever a batch request fails or partial data is missing, the Collector records a loss range in the `losses` table. This provides a complete audit trail for every missing interval.
- **Exactly‑once cloud delivery** – The SyncWorker only marks rows as `synced` after receiving a successful `{ "ok": true }` response. The FastAPI endpoint uses `ON CONFLICT DO NOTHING`, so duplicate uploads are ignored without side‑effects.

## 3. Recovery & Offline Operation
- The Collector detects socket errors, CRC mismatches or time‑outs and switches the device into **offline mode**. It continues to request batches, records losses, and queues samples locally.
- An exponential back‑off (2 s → 32 s) is used for reconnection attempts. Once the socket is re‑established, the mode flips back to **online** and normal collection resumes.
- The SyncWorker runs independently; on reconnection it uploads any backlog automatically.

## 4. Deterministic Processing (Processor)
- Operates on raw samples only – no timestamps are used, eliminating any source of nondeterminism.
- For each 1‑second window (400 samples per sensor) it computes:
  - Missing sample count per sensor.
  - Per‑channel min, max, mean, variance, and magnitude.
  - Heart‑rate detection via peak‑to‑peak interval analysis.
  - A `mode` flag (`online` / `offline`).
- The same module is imported by the device CLI (`pipeline results`) and can be imported by the cloud service to produce identical NDJSON output.

## 5. Resource Constraints (Raspberry‑Pi class)
| Resource | Target | Measurement Method |
|----------|--------|--------------------|
| CPU      | ≤ 8 % average per core during collection | `psutil.cpu_percent(interval=1)` logged by `measurements.py` |
| Memory   | ≤ 50 MiB resident set size                 | `psutil.Process().memory_info().rss` |
| Disk I/O | ≤ 5 MiB/s write (SQLite journal)          | `psutil.disk_io_counters()` |
| Network  | ≤ 200 KB/s outbound (batch uploads)        | `psutil.net_io_counters()` |

The `measurements.py` script runs for a configurable period (default 1 hour) and writes the recorded metrics to `artifacts/resource_report.json`.

## 6. Testing Strategy
- **Unit tests** cover each module’s public API and edge‑cases (partial reads, CRC validation, loss insertion, sync retries, idempotent DB inserts).
- **Integration tests** spin up the FastAPI server with an in‑memory PostgreSQL container and verify end‑to‑end upload and duplicate handling.
- **Performance tests** use `measurements.py` to ensure the system stays within the resource limits.

All tests are located under `tests/` and can be run with a single `pytest -v` command.
