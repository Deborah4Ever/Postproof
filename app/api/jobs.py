from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

from ..db.session import get_db
from ..db.models import Job
from ..db.schemas import JobResponse, JobUpdate, JobVerificationResult
from ..services.discovery_service import run_discovery_pipeline, TARGET_CATEGORIES
from ..services.verification_service import verify_job_record, verify_unverified_jobs_batch
from ..services.monid_service import monid_service

router = APIRouter(prefix="/api", tags=["Job Discovery & Research"])


# ----------------------------------------------------------------------
# Job Retrieval & Filtering
# ----------------------------------------------------------------------
@router.get("/jobs")
def list_jobs(
    q: str | None = None,
    category: str | None = None,
    source: str | None = None,
    status: str | None = None,
    remote: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Search and filter discovered jobs by keyword, category, source, verification status, and remote status.
    """
    query = db.query(Job)

    if q:
        q_wildcard = f"%{q}%"
        query = query.filter(
            or_(
                Job.job_title.ilike(q_wildcard),
                Job.company.ilike(q_wildcard),
                Job.description.ilike(q_wildcard),
                Job.location.ilike(q_wildcard),
            )
        )

    if category and category != "ALL":
        query = query.filter(Job.detected_category.ilike(f"%{category}%"))

    if source and source != "ALL":
        query = query.filter(Job.source.ilike(f"%{source}%"))

    if status and status != "ALL":
        query = query.filter(Job.status == status.upper())

    if remote and remote != "ALL":
        query = query.filter(Job.remote_status.ilike(f"%{remote}%"))

    total = query.count()
    jobs = query.order_by(Job.posted_at.desc().nullslast(), Job.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "jobs": [JobResponse.model_validate(j) for j in jobs],
    }


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job_details(job_id: str, db: Session = Depends(get_db)):
    """Retrieve full details of a specific job including verification audit."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.put("/jobs/{job_id}", response_model=JobResponse)
def update_job_details(job_id: str, job_update: JobUpdate, db: Session = Depends(get_db)):
    """Update fields on a job record."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    for field, val in job_update.model_dump(exclude_unset=True).items():
        setattr(job, field, val)

    db.commit()
    db.refresh(job)
    return job


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, db: Session = Depends(get_db)):
    """Delete a job record."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    db.delete(job)
    db.commit()
    return None


from pydantic import BaseModel, Field


class DiscoveryPayload(BaseModel):
    sources: list[str] | None = None
    category: str | None = None
    limit: int = 50


# ----------------------------------------------------------------------
# Job Discovery Actions
# ----------------------------------------------------------------------
@router.post("/jobs/discover")
def trigger_job_discovery(
    payload: DiscoveryPayload = DiscoveryPayload(),
    db: Session = Depends(get_db),
):
    """
    Executes live job discovery across permitted public feeds (WWR, Jobicy, Arbeitnow, Monid).
    Deduplicates and stores legitimate jobs in the database.
    """
    stats = run_discovery_pipeline(
        db=db,
        sources=payload.sources,
        category_filter=payload.category,
        limit=payload.limit,
    )
    return {
        "message": "Job discovery completed.",
        "stats": stats,
    }


@router.get("/categories")
def get_target_categories():
    """Returns the list of active target job categories."""
    return TARGET_CATEGORIES


# ----------------------------------------------------------------------
# Job Verification Actions
# ----------------------------------------------------------------------
@router.post("/jobs/{job_id}/verify")
def verify_single_job(job_id: str, db: Session = Depends(get_db)):
    """
    Executes the 8-point verification engine for a specific job:
    checks valid URL, identifiable company, duplicate status, availability,
    suspicious indicators, location compatibility, employment type, and job age.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    result = verify_job_record(job, db, check_network_live=True)
    return result


@router.post("/jobs/verify-batch")
def verify_batch_jobs(limit: int = 20, db: Session = Depends(get_db)):
    """Runs verification checks on a batch of unverified jobs."""
    results = verify_unverified_jobs_batch(db, limit=limit)
    return {
        "verified_count": len(results),
        "results": results,
    }


# ----------------------------------------------------------------------
# Monid Dedicated API Proxy (Secured, Key Never Exposed)
# ----------------------------------------------------------------------
@router.get("/monid/endpoints")
def monid_discover_endpoints(
    category: str | None = None,
    provider: str | None = None,
    q: str | None = None,
    limit: int = 50,
):
    """
    Server-side Monid endpoint discovery.
    Allows inspecting available tools without exposing MONID_API_KEY to frontend.
    """
    return monid_service.discover_endpoints(category=category, provider=provider, query=q, limit=limit)


@router.get("/monid/inspect")
def monid_inspect_endpoint(provider: str, endpoint: str):
    """Inspects a specific Monid tool/endpoint definition and pricing."""
    return monid_service.inspect_endpoint(provider=provider, endpoint=endpoint)
