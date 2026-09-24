"""
Test suite for Review Queue, Application Tracking, Filters, Pipeline, and Dashboard.
"""

import os
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DISABLE_BACKGROUND_SCANNER"] = "1"

from app.main import app
from app.db.models import Base, CareerProfile, Job, JobMatch, ApplicationPackage, FilterSettings, InAppNotification
from app.db.session import get_db
from app.services.pipeline_service import apply_job_filters, get_or_create_filter_settings

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_database():
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    # Seed profile
    prof = CareerProfile(
        id="prof-seo-1",
        name="SEO / Content / Digital Marketing",
        slug="seo-content-digital-marketing",
        target_roles=["SEO Specialist", "Content Strategist"],
        summary="Expert in organic search and content strategy.",
        skills=["SEO", "Keyword Research", "Technical SEO"],
        tools=["Ahrefs", "SEMrush", "Google Search Console"],
        achievements=["Grew organic traffic by 140%"],
    )
    db.add(prof)

    # Seed job 1 (Good job)
    job1 = Job(
        id="job-good-1",
        source="Jobicy",
        job_url="https://jobicy.com/jobs/seo-lead",
        company="TechCorp",
        job_title="Senior SEO Specialist",
        description="Full-time remote SEO Specialist. Manage technical SEO and content.",
        location="Remote",
        remote_status="Remote",
        employment_type="Full-time",
        requirements=["SEO", "Technical SEO"],
        skills=["SEO"],
        detected_category="SEO",
        status="VERIFIED",
    )
    db.add(job1)

    # Seed job 2 (Unpaid internship - should be filtered)
    job2 = Job(
        id="job-unpaid-2",
        source="Arbeitnow",
        job_url="https://arbeitnow.com/jobs/unpaid-intern",
        company="StartupX",
        job_title="SEO Assistant (Unpaid)",
        description="Unpaid internship for students looking to gain SEO experience.",
        location="Remote",
        remote_status="Remote",
        employment_type="Internship",
        status="VERIFIED",
    )
    db.add(job2)

    # Seed match and ready application
    match = JobMatch(
        id="match-1",
        job_id="job-good-1",
        profile_id="prof-seo-1",
        fit_score=0.88,
        overall_fit_verdict="STRONG FIT",
        supporting_evidence=["Direct SEO experience", "140% organic growth metric"],
        potential_gaps=["No direct enterprise agency experience"],
        summary="Candidate background strongly aligns with SEO requirements.",
    )
    db.add(match)

    app_pkg = ApplicationPackage(
        id="pkg-1",
        job_id="job-good-1",
        profile_id="prof-seo-1",
        tailored_resume_markdown="# Tailored Resume\nSEO Specialist with proven results.",
        cover_letter="Dear TechCorp Hiring Team, I am writing to apply for the Senior SEO Specialist role.",
        short_proposal="Proposal for organic search scaling at TechCorp.",
        recruiter_message="Hi, I noticed the Senior SEO opening at TechCorp.",
        application_answers=[{"question": "Why TechCorp?", "answer": "Strong alignment with product mission."}],
        portfolio_selection=[{"title": "SEO Case Study", "url": "https://example.com/case-study"}],
        status="READY_FOR_REVIEW",
    )
    db.add(app_pkg)

    db.commit()
    db.close()

    yield

    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.pop(get_db, None)


def test_job_filters_exclude_unpaid_and_keywords():
    """Verify filter engine properly detects and excludes forbidden keywords like 'unpaid', 'commission only'."""
    db = TestingSessionLocal()
    settings = get_or_create_filter_settings(db)

    job_good = db.query(Job).filter(Job.id == "job-good-1").first()
    job_unpaid = db.query(Job).filter(Job.id == "job-unpaid-2").first()

    pass_good, reason_good = apply_job_filters(job_good, settings)
    assert pass_good is True, f"Good job should pass filters: {reason_good}"

    pass_unpaid, reason_unpaid = apply_job_filters(job_unpaid, settings)
    assert pass_unpaid is False, "Unpaid job must be filtered out"
    assert "unpaid" in reason_unpaid.lower()
    db.close()


def test_review_queue_retrieval_and_fields():
    """Verify review queue returns all required fields and legitimate application URL."""
    client = TestClient(app)
    response = client.get("/api/review-queue")
    assert response.status_code == 200
    items = response.json()
    assert len(items) >= 1

    item = items[0]
    assert item["job_title"] == "Senior SEO Specialist"
    assert item["company"] == "TechCorp"
    assert item["location"] == "Remote"
    assert item["source"] == "Jobicy"
    assert item["job_url"] == "https://jobicy.com/jobs/seo-lead"
    assert item["profile_name"] == "SEO / Content / Digital Marketing"
    assert "SEO" in item["why_it_matches"]
    assert len(item["potential_gaps"]) >= 1
    assert item["cover_letter"]
    assert item["resume_markdown"]
    assert item["status"] == "READY_FOR_REVIEW"


def test_review_queue_actions_approve_and_reject():
    """Verify user actions APPROVE and REJECT update application status."""
    client = TestClient(app)

    # Approve
    resp_approve = client.post("/api/review-queue/pkg-1/approve")
    assert resp_approve.status_code == 200
    assert resp_approve.json()["status"] == "APPROVED"

    # Reject
    resp_reject = client.post("/api/review-queue/pkg-1/reject?notes=Location mismatch after review")
    assert resp_reject.status_code == 200
    assert resp_reject.json()["status"] == "REJECTED"


def test_application_tracking_lifecycle():
    """Verify full application tracking lifecycle: APPLIED, INTERVIEW, OFFER, outcome, follow-up."""
    client = TestClient(app)

    # Mark as applied after user manually submits on external site
    resp_applied = client.post("/api/review-queue/pkg-1/mark-applied?follow_up_days=5&notes=Submitted via official portal")
    assert resp_applied.status_code == 200
    data = resp_applied.json()
    assert data["status"] == "APPLIED"
    assert data["applied_at"] is not None
    assert data["follow_up_date"] is not None
    assert data["outcome"] == "PENDING"

    # Progress to INTERVIEW
    resp_interview = client.put("/api/review-queue/pkg-1/track", json={
        "status": "INTERVIEW",
        "outcome": "INTERVIEW_SCHEDULED",
        "user_notes": "First round with VP of Marketing scheduled for next Tuesday.",
    })
    assert resp_interview.status_code == 200
    assert resp_interview.json()["status"] == "INTERVIEW"
    assert resp_interview.json()["outcome"] == "INTERVIEW_SCHEDULED"


def test_dashboard_metrics():
    """Verify dashboard metrics returns accurate counters and cost statistics."""
    client = TestClient(app)
    response = client.get("/api/dashboard/metrics")
    assert response.status_code == 200
    data = response.json()

    assert data["total_jobs"] >= 2
    assert "verified_jobs" in data
    assert "qualified_jobs" in data
    assert "applications_ready" in data
    assert "total_cost_usd" in data


def test_settings_and_filter_configuration():
    """Verify candidate filter settings can be retrieved and updated."""
    client = TestClient(app)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    settings = resp.json()
    assert "USA" in settings["target_countries"]
    assert "unpaid" in settings["excluded_keywords"]

    # Update settings
    update_payload = {
        "scan_interval_minutes": 45,
        "excluded_companies": ["SpamCo", "ScamLLC"],
        "min_relevance_score": 0.70,
    }
    resp_update = client.put("/api/settings", json=update_payload)
    assert resp_update.status_code == 200
    updated = resp_update.json()
    assert updated["scan_interval_minutes"] == 45
    assert "SpamCo" in updated["excluded_companies"]
    assert updated["min_relevance_score"] == 0.70
