import argparse
import logging
import os
import sys
from pathlib import Path

# Import sub-modules (they will be created later)
from .collector import Collector
from .storage import Storage
from .processor import Processor
from .sync_worker import SyncWorker
from .cloud_client import CloudClient


def _load_env():
    """Load environment variables from .env if present (simple implementation)."""
    from dotenv import load_dotenv
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)


def _setup_logging():
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")


def main(argv=None):
    _load_env()
    _setup_logging()
    parser = argparse.ArgumentParser(prog="pipeline", description="MayaCardio device pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run: starts collector + sync (foreground)
    run_parser = subparsers.add_parser("run", help="Start collection and sync loop")
    run_parser.add_argument("--session", type=str, required=False, help="Optional session id override")

    # status
    subparsers.add_parser("status", help="Print JSON status line")

    # export
    export_parser = subparsers.add_parser("export", help="Export samples or loss records")
    export_parser.add_argument("--session", required=True, help="Session ID to export")
    export_parser.add_argument("--from", dest="source", choices=["device", "cloud"], required=True, help="Source of export")

    # results
    results_parser = subparsers.add_parser("results", help="Compute and output per‑window results")
    results_parser.add_argument("--session", required=True, help="Session ID to compute results for")

    args = parser.parse_args(argv)

    # Shared objects
    db_path = os.getenv("SQLITE_DB", "mayacardio.db")
    storage = Storage(db_path)
    cloud = CloudClient(base_url=os.getenv("CLOUD_URL", "http://localhost:8000"))

    if args.command == "run":
        # Start collector thread and sync worker thread; join indefinitely
        collector = Collector(storage=storage)
        sync = SyncWorker(storage=storage, cloud_client=cloud)
        collector.start()
        sync.start()
        try:
            collector.join()
            sync.join()
        except KeyboardInterrupt:
            logging.info("Interrupted, shutting down")
            collector.stop()
            sync.stop()
            sys.exit(0)

    elif args.command == "status":
        status = {
            "mode": storage.current_mode(),  # device mode: online/offline
            "samples_local": storage.count_samples(),
            "samples_cloud": storage.count_synced_samples(),
            "sync_backlog": storage.count_unsynced()
        }
        import json
        print(json.dumps(status))  # Proper single JSON line

    elif args.command == "export":
        exporter = Processor(storage)
        for line in exporter.export(session_id=args.session, source=args.source):
            print(line)

    elif args.command == "results":
        processor = Processor(storage)
        for line in processor.compute_results(session_id=args.session):
            print(line)
    else:
        parser.error("Unknown command")

if __name__ == "__main__":
    main()
