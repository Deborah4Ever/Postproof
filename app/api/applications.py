"""
API Router for Job Matching, Application Preparation, and Quality Review.
"""

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..db.models import Job, CareerProfile, JobMatch, ApplicationPackage
from ..db.schemas import (
    JobMatchResponse,
    GenerateApplicationRequest,
    ApplicationPackageResponse,
    ApplicationPackageUpdate,
    QualityReviewResult,
)
from ..services.matching_service import (
    save_or_update_job_match,
    recommend_profile_for_job,
)
from ..services.application_service import (
    generate_application_package,
    save_or_update_application_package,
)
from ..services.quality_review_service import (
    apply_quality_review_to_package,
    run_quality_review,
)

router = APIRouter(prefix="/api", tags=["Job Matching & Applications"])


def _format_match_response(match: JobMatch, db: Session) -> dict[str, Any]:
    """Helper to enrich JobMatch with recommended profile name."""
    rec_name = None
    if match.recommended_profile_id:
        rec_prof = db.query(CareerProfile).filter(CareerProfile.id == match.recommended_profile_id).first()
        if rec_prof:
            rec_name = rec_prof.name

    return {
        "id": match.id,
        "job_id": match.job_id,
        "profile_id": match.profile_id,
        "recommended_profile_id": match.recommended_profile_id,
        "recommended_profile_name": rec_name,
        "fit_score": match.fit_score,
        "overall_fit_verdict": match.overall_fit_verdict,
        "criteria_evaluations": match.criteria_evaluations or {},
        "supporting_evidence": match.supporting_evidence or [],
        "potential_gaps": match.potential_gaps or [],
        "summary": match.summary or "",
        "created_at": match.created_at,
    }


@router.post("/jobs/{job_id}/match", response_model=JobMatchResponse)
def match_job_against_profile(
    job_id: str,
    profile_id: str | None = Query(None, description="Optional profile ID to override recommendation"),
    db: Session = Depends(get_db),
):
    """
    Evaluates a verified job across all 10 matching criteria against candidate evidence.
    If profile_id is not specified, the system recommends the most appropriate profile.
    """
    try:
        match = save_or_update_job_match(job_id=job_id, selected_profile_id=profile_id, db=db)
        return _format_match_response(match, db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Match evaluation error: {str(e)}")


@router.get("/jobs/{job_id}/match", response_model=JobMatchResponse)
def get_job_match(
    job_id: str,
    db: Session = Depends(get_db),
):
    """Retrieves the current match evaluation for a job."""
    match = db.query(JobMatch).filter(JobMatch.job_id == job_id).order_by(JobMatch.created_at.desc()).first()
    if not match:
        # If no match yet, run match evaluation automatically
        try:
            match = save_or_update_job_match(job_id=job_id, db=db)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return _format_match_response(match, db)


@router.post("/jobs/{job_id}/generate-application", response_model=ApplicationPackageResponse)
def generate_application(
    job_id: str,
    req: GenerateApplicationRequest,
    db: Session = Depends(get_db),
):
    """
    Generates a truthful, tailored application package:
    - Tailored resume markdown
    - Cover letter
    - Short proposal
    - Recruiter message
    - Application Q&A answers
    - Curated portfolio selection

    Enforces zero fabrication and no em dashes.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    profiles = db.query(CareerProfile).all()
    if not profiles:
        raise HTTPException(status_code=400, detail="No career profiles found. Please create a profile first.")

    target_profile = None
    if req.profile_id:
        target_profile = db.query(CareerProfile).filter(CareerProfile.id == req.profile_id).first()

    if not target_profile:
        rec_prof, _ = recommend_profile_for_job(job, profiles)
        target_profile = rec_prof or profiles[0]

    # Fetch or generate job match context
    match = db.query(JobMatch).filter(
        JobMatch.job_id == job.id,
        JobMatch.profile_id == target_profile.id,
    ).first()
    if not match:
        match = save_or_update_job_match(job_id=job.id, selected_profile_id=target_profile.id, db=db)

    # Generate truthful content
    package_data = generate_application_package(
        job=job,
        profile=target_profile,
        db=db,
        custom_instructions=req.custom_instructions,
        match_info=match,
    )

    # Save to database
    package = save_or_update_application_package(
        job_id=job.id,
        profile_id=target_profile.id,
        package_data=package_data,
        db=db,
    )

    return package


@router.get("/jobs/{job_id}/application", response_model=ApplicationPackageResponse | None)
def get_job_application(
    job_id: str,
    db: Session = Depends(get_db),
):
    """Retrieves the application package for a job if one has been generated."""
    pkg = db.query(ApplicationPackage).filter(
        ApplicationPackage.job_id == job_id
    ).order_by(ApplicationPackage.created_at.desc()).first()
    return pkg


@router.get("/applications/{application_id}", response_model=ApplicationPackageResponse)
def get_application_by_id(
    application_id: str,
    db: Session = Depends(get_db),
):
    """Retrieves an application package by ID."""
    pkg = db.query(ApplicationPackage).filter(ApplicationPackage.id == application_id).first()
    if not pkg:
        raise HTTPException(status_code=404, detail=f"ApplicationPackage '{application_id}' not found.")
    return pkg


@router.put("/applications/{application_id}", response_model=ApplicationPackageResponse)
def update_application(
    application_id: str,
    payload: ApplicationPackageUpdate,
    db: Session = Depends(get_db),
):
    """Allows manual editing and review notes for an application package."""
    pkg = db.query(ApplicationPackage).filter(ApplicationPackage.id == application_id).first()
    if not pkg:
        raise HTTPException(status_code=404, detail=f"ApplicationPackage '{application_id}' not found.")

    if payload.tailored_resume_markdown is not None:
        pkg.tailored_resume_markdown = payload.tailored_resume_markdown
    if payload.cover_letter is not None:
        pkg.cover_letter = payload.cover_letter
    if payload.short_proposal is not None:
        pkg.short_proposal = payload.short_proposal
    if payload.recruiter_message is not None:
        pkg.recruiter_message = payload.recruiter_message
    if payload.application_answers is not None:
        pkg.application_answers = payload.application_answers
    if payload.portfolio_selection is not None:
        pkg.portfolio_selection = payload.portfolio_selection
    if payload.user_notes is not None:
        pkg.user_notes = payload.user_notes
    if payload.status is not None:
        pkg.status = payload.status

    db.commit()
    db.refresh(pkg)
    return pkg


@router.post("/applications/{application_id}/quality-review", response_model=ApplicationPackageResponse)
def perform_quality_review(
    application_id: str,
    db: Session = Depends(get_db),
):
    """
    Executes the second-pass Quality Review Audit.
    Checks:
    - factual accuracy
    - evidence support
    - relevance
    - grammar & naturalness
    - unsupported claims (BLOCKS the application)
    - contradictions
    - wrong company names / job titles
    - missing important requirements
    - em dashes and AI buzzwords
    """
    try:
        pkg = apply_quality_review_to_package(application_id, db)
        return pkg
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Quality review failed: {str(e)}")
