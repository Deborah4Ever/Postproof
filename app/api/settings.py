"""
API Router for Filter Settings, Candidate Exclusions, and Scheduler Control.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..db.schemas import FilterSettingsResponse, FilterSettingsUpdate
from ..services.pipeline_service import get_or_create_filter_settings, run_full_pipeline_scan
from ..services.scheduler_service import (
    update_scheduler_interval,
    get_scheduler_status,
    trigger_immediate_scan,
)

router = APIRouter(prefix="/api/settings", tags=["Settings & Filters"])


@router.get("", response_model=FilterSettingsResponse)
def get_settings(
    db: Session = Depends(get_db),
):
    """Retrieves current candidate filters and scanner settings."""
    return get_or_create_filter_settings(db)


@router.put("", response_model=FilterSettingsResponse)
def update_settings(
    payload: FilterSettingsUpdate,
    db: Session = Depends(get_db),
):
    """
    Updates candidate filters (countries, categories, excluded companies/keywords like unpaid,
    commission only, citizenship required), scan interval, and auto prep settings.
    """
    settings = get_or_create_filter_settings(db)

    if payload.target_countries is not None:
        settings.target_countries = payload.target_countries
    if payload.target_job_titles is not None:
        settings.target_job_titles = payload.target_job_titles
    if payload.target_categories is not None:
        settings.target_categories = payload.target_categories
    if payload.remote_types is not None:
        settings.remote_types = payload.remote_types
    if payload.employment_types is not None:
        settings.employment_types = payload.employment_types
    if payload.min_salary is not None:
        settings.min_salary = payload.min_salary
    if payload.max_job_age_days is not None:
        settings.max_job_age_days = payload.max_job_age_days
    if payload.min_relevance_score is not None:
        settings.min_relevance_score = payload.min_relevance_score
    if payload.excluded_companies is not None:
        settings.excluded_companies = payload.excluded_companies
    if payload.excluded_keywords is not None:
        settings.excluded_keywords = payload.excluded_keywords
    if payload.auto_prep_promising is not None:
        settings.auto_prep_promising = payload.auto_prep_promising
    if payload.notification_email is not None:
        settings.notification_email = payload.notification_email
    if payload.webhook_url is not None:
        settings.webhook_url = payload.webhook_url

    if payload.scan_interval_minutes is not None:
        settings.scan_interval_minutes = max(5, payload.scan_interval_minutes)
        update_scheduler_interval(settings.scan_interval_minutes)

    db.commit()
    db.refresh(settings)
    return settings


@router.post("/scan-now")
def trigger_manual_scan(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Triggers an immediate background discovery, verification, and matching scan cycle."""
    background_tasks.add_task(trigger_immediate_scan)
    return {"status": "started", "message": "Manual pipeline scan triggered in background"}


@router.get("/scheduler-status")
def get_scheduler_info():
    """Retrieves operational status of the background scanner."""
    return get_scheduler_status()
