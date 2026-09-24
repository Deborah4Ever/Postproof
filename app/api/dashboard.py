"""
API Router for Dashboard Metrics, Notifications, and Cost Tracking.
"""

from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..db.session import get_db
from ..db.models import Job, ApplicationPackage, InAppNotification, CostEvent
from ..db.schemas import DashboardMetricsResponse, NotificationResponse, CostEventResponse
from ..services.scheduler_service import get_scheduler_status
from ..monid_client import total_measured_cost

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard & Notifications"])


@router.get("/metrics", response_model=DashboardMetricsResponse)
def get_dashboard_metrics(
    db: Session = Depends(get_db),
):
    """
    Computes key operational dashboard metrics:
    - New jobs (discovered in last 24h)
    - Verified jobs
    - Qualified jobs
    - Applications ready for review
    - Applications submitted
    - Interviews
    - Follow-ups (due soon)
    - Monid & AI cost metrics
    """
    now = datetime.now(timezone.utc)
    one_day_ago = now - timedelta(hours=24)
    seven_days_from_now = now + timedelta(days=7)

    total_jobs = db.query(Job).count()
    new_jobs_24h = db.query(Job).filter(Job.created_at >= one_day_ago).count()
    verified_jobs = db.query(Job).filter(Job.status == "VERIFIED").count()
    qualified_jobs = db.query(Job).filter(Job.status == "QUALIFIED").count()

    applications_draft = db.query(ApplicationPackage).filter(
        ApplicationPackage.status.in_(["DRAFT", "NEEDS_REVIEW"])
    ).count()

    applications_ready = db.query(ApplicationPackage).filter(
        ApplicationPackage.status.in_(["READY_FOR_REVIEW", "READY"])
    ).count()

    applications_approved = db.query(ApplicationPackage).filter(
        ApplicationPackage.status == "APPROVED"
    ).count()

    applications_submitted = db.query(ApplicationPackage).filter(
        ApplicationPackage.status == "APPLIED"
    ).count()

    interviews = db.query(ApplicationPackage).filter(
        ApplicationPackage.status == "INTERVIEW"
    ).count()

    follow_ups = db.query(ApplicationPackage).filter(
        ApplicationPackage.follow_up_date.isnot(None),
        ApplicationPackage.follow_up_date <= seven_days_from_now,
        ApplicationPackage.status.in_(["APPLIED", "INTERVIEW"]),
    ).count()

    # Cost calculations
    ai_cost_sum = db.query(func.sum(CostEvent.cost_usd)).filter(CostEvent.service == "llm").scalar() or 0.0
    monid_data = total_measured_cost()
    monid_spend = float(monid_data) if isinstance(monid_data, (int, float)) else float(monid_data.get("total_measured_cost_usd", 0.0))
    total_cost = ai_cost_sum + monid_spend

    sched_status = get_scheduler_status()
    last_scan = None
    if sched_status.get("last_scan_at"):
        try:
            last_scan = datetime.fromisoformat(sched_status["last_scan_at"])
        except Exception:
            last_scan = None

    return {
        "total_jobs": total_jobs,
        "new_jobs_24h": new_jobs_24h,
        "verified_jobs": verified_jobs,
        "qualified_jobs": qualified_jobs,
        "applications_draft": applications_draft,
        "applications_ready": applications_ready,
        "applications_approved": applications_approved,
        "applications_submitted": applications_submitted,
        "interviews": interviews,
        "follow_ups": follow_ups,
        "total_cost_usd": round(total_cost, 4),
        "monid_spend_usd": round(monid_spend, 4),
        "ai_spend_usd": round(ai_cost_sum, 4),
        "last_scan_at": last_scan,
    }


@router.get("/notifications", response_model=list[NotificationResponse])
def get_notifications(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Retrieves recent in-app notifications."""
    notifs = db.query(InAppNotification).order_by(
        InAppNotification.created_at.desc()
    ).limit(limit).all()
    return notifs


@router.post("/notifications/{notif_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    notif_id: str,
    db: Session = Depends(get_db),
):
    """Marks a notification as read."""
    n = db.query(InAppNotification).filter(InAppNotification.id == notif_id).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.read = True
    db.commit()
    db.refresh(n)
    return n


@router.post("/notifications/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
):
    """Marks all unread notifications as read."""
    db.query(InAppNotification).filter(InAppNotification.read == False).update({"read": True})
    db.commit()
    return {"status": "success"}


@router.get("/costs", response_model=list[CostEventResponse])
def get_recent_costs(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Returns recent cost logging events."""
    events = db.query(CostEvent).order_by(CostEvent.created_at.desc()).limit(limit).all()
    return events
