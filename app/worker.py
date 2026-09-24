"""
Standalone Worker Script for Railway Cron & Background Process Execution.

Usage:
  python -m app.worker              # Runs an immediate full scan and exits
  python -m app.worker --daemon     # Runs continuous scheduled scanner in daemon mode
"""

import sys
import time
import argparse
import logging
from .db.session import SessionLocal
from .services.pipeline_service import run_full_pipeline_scan
from .services.scheduler_service import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("worker")


def main():
    parser = argparse.ArgumentParser(description="AI Job Agent Background Worker")
    parser.add_argument("--daemon", action="store_true", help="Run continuously as background daemon")
    args = parser.parse_args()

    if args.daemon:
        logger.info("Starting worker in continuous background daemon mode...")
        start_scheduler()
        try:
            while True:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Worker shutting down.")
            sys.exit(0)
    else:
        logger.info("Executing standalone pipeline scan...")
        db = SessionLocal()
        try:
            stats = run_full_pipeline_scan(db)
            logger.info(f"Pipeline scan complete. Results: {stats}")
        finally:
            db.close()


if __name__ == "__main__":
    main()
