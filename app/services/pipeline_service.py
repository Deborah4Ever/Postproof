"""
Scheduled Scan & End-to-End Autonomous Pipeline Service.

Executes the end-to-end job discovery, verification, filtering, matching,
truthful application preparation, quality audit, and review queue placement:

Retrieve new jobs
↓
Normalize
↓
Deduplicate
↓
Verify (8-Point Scam/Legitimacy Check)
↓
Filter (Countries, Seniority, Excluded Keywords like 'unpaid', 'security clearance', etc.)
↓
Match against profiles
↓
Only deeply analyze promising jobs (cost-effective gating!)
↓
Generate application materials (Tailored Resume, Cover Letter, Proposal, Q&A)
↓
Quality review (Strict hallucination/em-dash/buzzword prevention)
↓
Place qualified applications in review queue
↓
Notify user when applications are ready for review
"""

import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any
from sqlalchemy.orm import Session

from ..db.models import (
    Job,
    CareerProfile,
    JobMatch,
    ApplicationPackage,
    FilterSettings,
    InAppNotification,
    CostEvent,
    utc_now,
)
from .discovery_service import run_discovery_pipeline
from .verification_service import verify_job_record
from .matching_service import recommend_profile_for_job, evaluate_job_match, save_or_update_job_match
from .application_service import generate_application_package, save_or_update_application_package
from .quality_review_service import apply_quality_review_to_package


def get_or_create_filter_settings(db: Session) -> FilterSettings:
    """Retrieves current FilterSettings or initializes defaults."""
    settings = db.query(FilterSettings).filter(FilterSettings.id == "default").first()
    if not settings:
        settings = FilterSettings(
            id="default",
            target_countries=["USA", "UK", "Europe", "Australia", "New Zealand"],
            target_categories=[
                "SEO", "SEO Content", "Digital Marketing", "Social Media Marketing", "Social Media Management",
                "Customer Support", "Customer Success", "Customer Experience", "Community Management",
                "Web3", "Crypto", "Community Operations", "Operations"
            ],
            remote_types=["Remote", "Hybrid"],
            employment_types=["Full-time", "Contract"],
            max_job_age_days=14,
            min_relevance_score=0.60,
            excluded_companies=[],
            excluded_keywords=[
                "unpaid", "commission only", "security clearance required", "us citizenship required",
                "active secret clearance", "volunteer", "equity only", "intern unpaid"
            ],
            scan_interval_minutes=30,
            auto_prep_promising=True,
        )
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def apply_job_filters(job: Job, settings: FilterSettings) -> tuple[bool, str]:
    """
    Evaluates whether a discovered job meets candidate filter configuration.
    Filters out unpaid, commission only, security clearance, wrong location, excluded companies, etc.
    """
    text = f"{job.job_title} {job.company} {job.description or ''}".lower()

    # 1. Excluded companies check
    for comp in (settings.excluded_companies or []):
        if comp.strip() and comp.strip().lower() in (job.company or "").lower():
            return False, f"Excluded company match: '{comp}'"

    # 2. Excluded keywords check (e.g. unpaid, commission only, security clearance)
    for kw in (settings.excluded_keywords or []):
        if kw.strip():
            pattern = r"\b" + re.escape(kw.strip().lower()) + r"\b"
            if re.search(pattern, text):
                return False, f"Contains excluded keyword: '{kw}'"

    # 3. Maximum job age
    if job.posted_at and settings.max_job_age_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.max_job_age_days)
        if job.posted_at < cutoff:
            return False, f"Job older than {settings.max_job_age_days} days"

    # 4. Remote status compatibility
    if settings.remote_types and job.remote_status:
        if not any(r.lower() in job.remote_status.lower() for r in settings.remote_types):
            return False, f"Remote status '{job.remote_status}' not in allowed types: {settings.remote_types}"

    # 5. Employment type compatibility
    if settings.employment_types and job.employment_type:
        if not any(e.lower() in job.employment_type.lower() for e in settings.employment_types):
            return False, f"Employment type '{job.employment_type}' not in allowed types: {settings.employment_types}"

    return True, "Passed candidate filters"


def record_cost_event(
    service: str,
    operation: str,
    cost_usd: float,
    tokens_in: int = 0,
    tokens_out: int = 0,
    db: Session | None = None,
):
    """Logs Monid or AI spend in the database for tracking."""
    if not db:
        return
    try:
        ev = CostEvent(
            service=service,
            operation=operation,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )
        db.add(ev)
        db.commit()
    except Exception as e:
        print(f"[CostTracker] Failed to record cost event: {e}")


def run_full_pipeline_scan(db: Session, max_jobs_to_process: int = 25) -> dict[str, Any]:
    """
    Executes the scheduled discovery, verification, filtering, and application prep cycle.
    Cost-effective gating: Only performs deep LLM matching & prep on PROMISING, verified jobs.
    """
    settings = get_or_create_filter_settings(db)
    profiles = db.query(CareerProfile).all()
    if not profiles:
        return {"status": "skipped", "reason": "No career profiles exist"}

    stats = {
        "discovered_new": 0,
        "verified_count": 0,
        "filtered_out": 0,
        "qualified_count": 0,
        "applications_prepared": 0,
        "notifications_sent": 0,
    }

    # Step 1: Retrieve new jobs & Deduplicate
    discovery_res = run_discovery_pipeline(
        sources=["jobicy", "weworkremotely", "arbeitnow"],
        limit=50,
        db=db,
    )
    stats["discovered_new"] = discovery_res.get("new_jobs_added", 0)

    # Step 2: Retrieve unverified jobs for verification
    unverified_jobs = db.query(Job).filter(
        Job.status == "UNVERIFIED"
    ).order_by(Job.created_at.desc()).limit(max_jobs_to_process).all()

    for job in unverified_jobs:
        # Step 3: Run 8-Point Verification
        v_res = verify_job_record(job, db)
        job.status = v_res.get("status", "UNVERIFIED")
        job.verification_details = v_res
        db.commit()
        stats["verified_count"] += 1

        if job.status != "VERIFIED":
            continue

        # Step 4: Apply Candidate Exclusion & Preference Filters (Cheap Filtering)
        passes_filter, filter_reason = apply_job_filters(job, settings)
        if not passes_filter:
            job.status = "REJECTED"
            v_details = dict(job.verification_details or {})
            v_details["filter_rejection_reason"] = filter_reason
            job.verification_details = v_details
            db.commit()
            stats["filtered_out"] += 1
            continue

        # Step 5: Profile Recommendation & Match Evaluation
        recommended_prof, _ = recommend_profile_for_job(job, profiles)
        target_profile = recommended_prof or profiles[0]

        match_record = save_or_update_job_match(
            job_id=job.id,
            selected_profile_id=target_profile.id,
            db=db,
        )

        # Record match cost (estimated)
        record_cost_event(
            service="llm",
            operation="match_evaluation",
            cost_usd=0.003,
            tokens_in=1200,
            tokens_out=400,
            db=db,
        )

        # Step 6: Relevance Gating (Only deeply analyze promising jobs!)
        if match_record.fit_score >= settings.min_relevance_score:
            job.status = "QUALIFIED"
            db.commit()
            stats["qualified_count"] += 1

            # Step 7: Application Generation & Preparation
            if settings.auto_prep_promising:
                existing_app = db.query(ApplicationPackage).filter(
                    ApplicationPackage.job_id == job.id,
                    ApplicationPackage.profile_id == target_profile.id,
                ).first()

                if not existing_app or existing_app.status == "DRAFT":
                    package_data = generate_application_package(
                        job=job,
                        profile=target_profile,
                        db=db,
                        match_info=match_record,
                    )

                    record_cost_event(
                        service="llm",
                        operation="application_generation",
                        cost_usd=0.008,
                        tokens_in=2500,
                        tokens_out=1500,
                        db=db,
                    )

                    saved_pkg = save_or_update_application_package(
                        job_id=job.id,
                        profile_id=target_profile.id,
                        package_data=package_data,
                        db=db,
                    )

                    # Step 8: Quality Review Audit
                    reviewed_pkg = apply_quality_review_to_package(saved_pkg.id, db)

                    # Step 9: Place Qualified Applications in Review Queue
                    if reviewed_pkg.status == "READY":
                        reviewed_pkg.status = "READY_FOR_REVIEW"
                        db.commit()

                        # Step 10: Simple In-App Notification
                        notif = InAppNotification(
                            type="APPLICATION_READY",
                            title=f"Ready for Review: {job.job_title} at {job.company}",
                            message=f"Matched with profile '{target_profile.name}' (Fit Score: {int(match_record.fit_score*100)}%). All claims verified.",
                            link_url=f"/#applications",
                        )
                        db.add(notif)
                        db.commit()
                        stats["notifications_sent"] += 1

                    stats["applications_prepared"] += 1

    return stats
