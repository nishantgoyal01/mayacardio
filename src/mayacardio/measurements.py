import time
import json
import psutil
import os
from pathlib import Path

# ---------------------------------------------------------------------
# Simple resource‑measurement helper
# ---------------------------------------------------------------------
# Records CPU, memory, disk I/O and network usage at a configurable
# interval for a configurable duration.  The results are written as a
# JSON list to ``artifacts/resource_report.json`` (relative to the
# project root).  This script is intended for the one‑hour benchmark
# required by the assessment but can be used for any period.
# ---------------------------------------------------------------------

def measure(duration_seconds: int = 3600, interval: float = 1.0) -> list[dict]:
    """Collect resource metrics for ``duration_seconds`` seconds.

    Each entry in the returned list contains a timestamp (ISO‑8601) and
    the values returned by ``psutil`` for CPU percent, resident memory
    (bytes), cumulative disk write bytes, and cumulative network sent
    bytes.
    """
    start = time.time()
    results = []
    # initial counters for delta calculations
    net_counters = psutil.net_io_counters()
    disk_counters = psutil.disk_io_counters()
    while (elapsed := time.time() - start) < duration_seconds:
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.Process().memory_info().rss
        # calculate deltas since start
        net = psutil.net_io_counters()
        disk = psutil.disk_io_counters()
        net_sent = net.bytes_sent - net_counters.bytes_sent
        disk_written = disk.write_bytes - disk_counters.write_bytes
        results.append({
            "timestamp": timestamp,
            "cpu_percent": cpu,
            "memory_rss_bytes": mem,
            "net_sent_bytes": net_sent,
            "disk_written_bytes": disk_written,
            "elapsed_seconds": round(elapsed, 2),
        })
        time.sleep(interval)
    return results


def main():
    # Respect environment overrides for CI / debugging
    duration = int(os.getenv("MEASURE_DURATION", "3600"))
    interval = float(os.getenv("MEASURE_INTERVAL", "1.0"))
    data = measure(duration_seconds=duration, interval=interval)
    # Write to the artifacts directory expected by the assessment
    artifacts_dir = Path(__file__).resolve().parents[2] / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_path = artifacts_dir / "resource_report.json"
    with out_path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2)
    print(f"Resource report written to {out_path}")


if __name__ == "__main__":
    main()
