"""
API Router for Review Queue and Application Tracking.

Provides endpoints for reviewing generated applications:
- Job title, company, location, source, original URL
- Recommended profile, why it matches, potential gaps
- Resume, cover letter, proposal, recruiter message, screening answers
- Actions: EDIT, APPROVE, REJECT, OPEN APPLICATION (external URL)
- Full lifecycle status tracking: DISCOVERED, VERIFIED, QUALIFIED, DRAFT, READY_FOR_REVIEW,
  APPROVED, APPLIED, REJECTED, INTERVIEW, OFFER, CLOSED
"""

from typing import Any
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..db.models import Job, CareerProfile, JobMatch, ApplicationPackage, utc_now
from ..db.schemas import ReviewQueueItemResponse, ApplicationPackageUpdate

router = APIRouter(prefix="/api/review-queue", tags=["Review Queue & Application Tracking"])


def _build_queue_item(pkg: ApplicationPackage, db: Session) -> dict[str, Any]:
    """Enriches an ApplicationPackage with job and matching evidence context."""
    job = db.query(Job).filter(Job.id == pkg.job_id).first()
    profile = db.query(CareerProfile).filter(CareerProfile.id == pkg.profile_id).first()
    match = db.query(JobMatch).filter(
        JobMatch.job_id == pkg.job_id,
        JobMatch.profile_id == pkg.profile_id,
    ).first()

    return {
        "id": pkg.id,
        "job_id": pkg.job_id,
        "job_title": job.job_title if job else "Unknown Job",
        "company": job.company if job else "Unknown Company",
        "location": job.location if job else "Remote",
        "remote_status": job.remote_status if job else "Remote",
        "source": job.source if job else "Direct",
        "job_url": job.job_url if job else "#",
        "profile_id": pkg.profile_id,
        "profile_name": profile.name if profile else "General Profile",
        "why_it_matches": match.summary if match else "Profile requirements matched candidate evidence.",
        "potential_gaps": match.potential_gaps if match else [],
        "resume_markdown": pkg.tailored_resume_markdown or "",
        "cover_letter": pkg.cover_letter or "",
        "short_proposal": pkg.short_proposal or "",
        "recruiter_message": pkg.recruiter_message or "",
        "application_answers": pkg.application_answers or [],
        "portfolio_selection": pkg.portfolio_selection or [],
        "status": pkg.status,
        "quality_review": pkg.quality_review or {},
        "user_notes": pkg.user_notes or "",
        "applied_at": pkg.applied_at,
        "follow_up_date": pkg.follow_up_date,
        "outcome": pkg.outcome or "PENDING",
        "created_at": pkg.created_at,
        "updated_at": pkg.updated_at,
    }


@router.get("", response_model=list[ReviewQueueItemResponse])
def get_review_queue(
    status_filter: str | None = Query(None, description="Optional status filter e.g. READY_FOR_REVIEW, APPROVED, APPLIED"),
    db: Session = Depends(get_db),
):
    """
    Returns applications in the review queue.
    If no status_filter is passed, defaults to actionable items (READY_FOR_REVIEW, READY, DRAFT, APPROVED).
    """
    query = db.query(ApplicationPackage)
    if status_filter and status_filter != "ALL":
        query = query.filter(ApplicationPackage.status == status_filter)
    else:
        # Default view shows ready or in-progress applications
        query = query.filter(
            ApplicationPackage.status.in_([
                "READY_FOR_REVIEW", "READY", "APPROVED", "APPLIED", "INTERVIEW", "DRAFT"
            ])
        )

    packages = query.order_by(ApplicationPackage.updated_at.desc()).all()
    return [_build_queue_item(pkg, db) for pkg in packages]


@router.get("/all", response_model=list[ReviewQueueItemResponse])
def get_all_tracked_applications(
    db: Session = Depends(get_db),
):
    """Returns all tracked applications across all lifecycle statuses."""
    packages = db.query(ApplicationPackage).order_by(ApplicationPackage.updated_at.desc()).all()
    return [_build_queue_item(pkg, db) for pkg in packages]


@router.post("/{app_id}/approve", response_model=ReviewQueueItemResponse)
def approve_application(
    app_id: str,
    db: Session = Depends(get_db),
):
    """
    Action: APPROVE.
    User approves the verified application materials for manual submission.
    """
    pkg = db.query(ApplicationPackage).filter(ApplicationPackage.id == app_id).first()
    if not pkg:
        raise HTTPException(status_code=404, detail="Application package not found")

    pkg.status = "APPROVED"
    db.commit()
    db.refresh(pkg)
    return _build_queue_item(pkg, db)


@router.post("/{app_id}/reject", response_model=ReviewQueueItemResponse)
def reject_application(
    app_id: str,
    notes: str | None = Query(None, description="Optional rejection notes"),
    db: Session = Depends(get_db),
):
    """
    Action: REJECT.
    User rejects the application from the queue.
    """
    pkg = db.query(ApplicationPackage).filter(ApplicationPackage.id == app_id).first()
    if not pkg:
        raise HTTPException(status_code=404, detail="Application package not found")

    pkg.status = "REJECTED"
    if notes:
        pkg.user_notes = f"{pkg.user_notes or ''}\nRejection note: {notes}".strip()
    db.commit()
    db.refresh(pkg)
    return _build_queue_item(pkg, db)


@router.post("/{app_id}/mark-applied", response_model=ReviewQueueItemResponse)
def mark_application_applied(
    app_id: str,
    follow_up_days: int = Query(7, description="Number of days from today for follow-up reminder"),
    notes: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Action: Mark as APPLIED after user manually submits through the legitimate job application page.
    Automatically records applied_at and sets follow-up date.
    """
    pkg = db.query(ApplicationPackage).filter(ApplicationPackage.id == app_id).first()
    if not pkg:
        raise HTTPException(status_code=404, detail="Application package not found")

    now = datetime.now(timezone.utc)
    pkg.status = "APPLIED"
    pkg.applied_at = now
    pkg.follow_up_date = now + (datetime.resolution * 0 if follow_up_days == 0 else datetime.now(timezone.utc) - datetime.now(timezone.utc) + (now - now + (now - now)) ) # helper
    from datetime import timedelta
    pkg.follow_up_date = now + timedelta(days=follow_up_days)
    pkg.outcome = "PENDING"
    if notes:
        pkg.user_notes = f"{pkg.user_notes or ''}\nSubmission note: {notes}".strip()

    db.commit()
    db.refresh(pkg)
    return _build_queue_item(pkg, db)


@router.put("/{app_id}/track", response_model=ReviewQueueItemResponse)
def update_application_tracking(
    app_id: str,
    payload: ApplicationPackageUpdate,
    db: Session = Depends(get_db),
):
    """
    Updates application tracking status and lifecycle fields:
    (e.g., INTERVIEW, OFFER, CLOSED, outcome, follow_up_date, notes).
    """
    pkg = db.query(ApplicationPackage).filter(ApplicationPackage.id == app_id).first()
    if not pkg:
        raise HTTPException(status_code=404, detail="Application package not found")

    if payload.status is not None:
        pkg.status = payload.status
    if payload.applied_at is not None:
        pkg.applied_at = payload.applied_at
    if payload.follow_up_date is not None:
        pkg.follow_up_date = payload.follow_up_date
    if payload.outcome is not None:
        pkg.outcome = payload.outcome
    if payload.user_notes is not None:
        pkg.user_notes = payload.user_notes
    if payload.tailored_resume_markdown is not None:
        pkg.tailored_resume_markdown = payload.tailored_resume_markdown
    if payload.cover_letter is not None:
        pkg.cover_letter = payload.cover_letter
    if payload.short_proposal is not None:
        pkg.short_proposal = payload.short_proposal
    if payload.recruiter_message is not None:
        pkg.recruiter_message = payload.recruiter_message

    db.commit()
    db.refresh(pkg)
    return _build_queue_item(pkg, db)
