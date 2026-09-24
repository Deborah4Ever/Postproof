"""
Background Job Scanner & Scheduler Service.

Manages scheduled scans (default every 30 minutes) using APScheduler.
- Runs without blocking the FastAPI event loop
- Prevents overlapping scans with execution locks
- Configurable scan interval
- Supports manual trigger on demand
"""

import logging
import threading
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..db.session import SessionLocal
from .pipeline_service import run_full_pipeline_scan, get_or_create_filter_settings

logger = logging.getLogger("scanner_scheduler")

_scheduler: BackgroundScheduler | None = None
_scan_lock = threading.Lock()
_last_scan_time: datetime | None = None
_last_scan_stats: dict | None = None
_is_running = False


def _scheduled_scan_job():
    """Target job invoked by the scheduler."""
    global _last_scan_time, _last_scan_stats, _is_running

    if not _scan_lock.acquire(blocking=False):
        logger.info("[Scheduler] Scan already in progress, skipping tick.")
        return

    _is_running = True
    db = SessionLocal()
    try:
        logger.info("[Scheduler] Starting scheduled job scan and matching cycle...")
        stats = run_full_pipeline_scan(db)
        _last_scan_time = datetime.now(timezone.utc)
        _last_scan_stats = stats
        logger.info(f"[Scheduler] Scan cycle complete: {stats}")
    except Exception as e:
        logger.error(f"[Scheduler] Scan cycle failed with error: {e}")
    finally:
        db.close()
        _is_running = False
        _scan_lock.release()


def start_scheduler():
    """Initializes and starts the APScheduler background thread."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    db = SessionLocal()
    try:
        settings = get_or_create_filter_settings(db)
        interval = settings.scan_interval_minutes or 30
    finally:
        db.close()

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _scheduled_scan_job,
        trigger=IntervalTrigger(minutes=interval),
        id="scheduled_job_scanner",
        name="Discover, Verify, Match and Prep Jobs",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(f"[Scheduler] Background scanner started with interval of {interval} minutes.")


def stop_scheduler():
    """Stops the scheduler gracefully."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Background scanner stopped.")


def update_scheduler_interval(minutes: int):
    """Reschedules the scanner with a new interval in minutes."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.reschedule_job(
            "scheduled_job_scanner",
            trigger=IntervalTrigger(minutes=max(5, minutes)),
        )
        logger.info(f"[Scheduler] Rescheduled scan interval to {minutes} minutes.")


def trigger_immediate_scan() -> dict:
    """Manually triggers an immediate scan cycle."""
    _scheduled_scan_job()
    return _last_scan_stats or {"status": "completed"}


def get_scheduler_status() -> dict:
    """Returns current scheduler operational metrics."""
    return {
        "running": _scheduler.running if _scheduler else False,
        "is_scanning_now": _is_running,
        "last_scan_at": _last_scan_time.isoformat() if _last_scan_time else None,
        "last_scan_stats": _last_scan_stats,
    }
