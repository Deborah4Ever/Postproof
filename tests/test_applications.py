"""
Test suite for Job Matching, Application Preparation, and Quality Review Audit.
"""

import os
os.environ["DISABLE_BACKGROUND_SCANNER"] = "1"
os.environ["ANTHROPIC_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.models import Base, CareerProfile, ExperienceRecord, Job, JobMatch, ApplicationPackage
from app.db.session import get_db
from app.services.matching_service import recommend_profile_for_job, evaluate_job_match, EVALUATION_CRITERIA
from app.services.application_service import generate_rule_based_application, _sanitize_em_dashes
from app.services.quality_review_service import rule_based_quality_audit

# Test SQLite in-memory database
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

    # Seed sample profile
    cs_profile = CareerProfile(
        id="cs-prof-1",
        name="Customer Support / Customer Success",
        slug="customer-support-customer-success",
        target_roles=["Customer Support Specialist", "Customer Success Manager", "Technical Support Representative"],
        summary="Experienced Customer Success Specialist with 4 years in SaaS ticketing and Zendesk management.",
        skills=["Customer Support", "Zendesk", "Ticketing", "Churn Reduction", "Client Onboarding"],
        tools=["Zendesk", "Intercom", "Jira", "Slack", "Notion"],
        achievements=["Maintained 98% CSAT across 10,000+ customer tickets"],
        portfolio_links=[{"title": "CSAT Playbook", "url": "https://example.com/playbook"}],
    )
    db.add(cs_profile)

    # Seed experience record
    exp = ExperienceRecord(
        profile_id="cs-prof-1",
        company="HelpDesk Global",
        role="Senior Customer Support Specialist",
        dates="2021 - 2024",
        responsibilities=["Resolved Tier 2 customer tickets", "Onboarded new enterprise clients"],
        achievements=["Maintained 98% CSAT across 10,000+ customer tickets", "Reduced resolution time by 30%"],
        metrics=["98% CSAT", "30% faster resolution"],
        tools=["Zendesk", "Intercom", "Jira"],
        skills=["Customer Support", "Ticket Resolution"],
        evidence=[{"title": "Performance Review", "url": "https://example.com/review", "quote": "Top CSAT rating"}],
    )
    db.add(exp)

    # Seed sample job
    job = Job(
        id="job-1",
        source="Ashby",
        job_url="https://jobs.ashbyhq.com/example/support",
        company="Acme SaaS",
        job_title="Customer Success Manager",
        description="We are seeking a Customer Success Manager with Zendesk ticketing experience, client onboarding skills, and proven track record of maintaining high CSAT. Enterprise SaaS experience preferred.",
        location="Remote",
        remote_status="Remote",
        employment_type="Full-time",
        requirements=["Customer support ticketing experience", "Zendesk proficiency", "5 years enterprise SaaS onboarding"],
        skills=["Customer Support", "Zendesk", "SaaS"],
        detected_category="Customer Support",
        status="VERIFIED",
    )
    db.add(job)
    db.commit()
    db.close()

    yield

    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.pop(get_db, None)


def test_profile_recommendation():
    """Verify profile recommendation engine correctly maps job to appropriate profile."""
    db = TestingSessionLocal()
    job = db.query(Job).filter(Job.id == "job-1").first()
    profiles = db.query(CareerProfile).all()

    recommended, reason = recommend_profile_for_job(job, profiles)
    assert recommended is not None
    assert recommended.id == "cs-prof-1"
    assert "Customer" in recommended.name
    db.close()


def test_matching_criteria_evaluation():
    """Verify all 10 criteria are evaluated and classified with real reasons and zero invented evidence."""
    db = TestingSessionLocal()
    job = db.query(Job).filter(Job.id == "job-1").first()
    profile = db.query(CareerProfile).filter(CareerProfile.id == "cs-prof-1").first()

    eval_result = evaluate_job_match(job, profile, db)
    assert "criteria_evaluations" in eval_result
    criteria = eval_result["criteria_evaluations"]

    # Verify all 10 criteria are present
    for crit in EVALUATION_CRITERIA:
        assert crit in criteria, f"Missing criterion: {crit}"
        assert criteria[crit]["status"] in ["MATCH", "PARTIAL MATCH", "MISSING", "UNKNOWN"]
        assert len(criteria[crit]["reason"]) > 0

    # Required skills should match based on Zendesk / Customer Support
    assert criteria["required_skills"]["status"] in ["MATCH", "PARTIAL MATCH"]
    assert "Zendesk" in criteria["tools"]["evidence"] or criteria["tools"]["status"] in ["MATCH", "PARTIAL MATCH"]

    # Check that potential gaps and supporting evidence are identified
    assert len(eval_result["supporting_evidence"]) > 0
    db.close()


def test_api_matching_and_override():
    """Test API endpoint POST /api/jobs/{id}/match and profile override."""
    client = TestClient(app)

    response = client.post("/api/jobs/job-1/match")
    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == "job-1"
    assert data["profile_id"] == "cs-prof-1"
    assert "criteria_evaluations" in data
    assert len(data["criteria_evaluations"]) == 10

    # Test override
    response_override = client.post("/api/jobs/job-1/match?profile_id=cs-prof-1")
    assert response_override.status_code == 200


def test_application_generation_and_no_em_dashes():
    """Verify application generator creates all assets and contains no em dashes."""
    client = TestClient(app)

    gen_payload = {
        "profile_id": "cs-prof-1",
        "include_proposal": True,
        "include_recruiter_message": True,
        "custom_instructions": "Focus on high CSAT and Zendesk experience.",
    }
    response = client.post("/api/jobs/job-1/generate-application", json=gen_payload)
    assert response.status_code == 200
    app_data = response.json()

    assert app_data["tailored_resume_markdown"]
    assert app_data["cover_letter"]
    assert app_data["short_proposal"]
    assert app_data["recruiter_message"]
    assert len(app_data["application_answers"]) >= 2
    assert app_data["status"] == "DRAFT"

    # Strictly check for NO em dashes in any of the fields
    for field in ["tailored_resume_markdown", "cover_letter", "short_proposal", "recruiter_message"]:
        content = app_data[field]
        assert "—" not in content, f"Em dash found in {field}"
        assert "–" not in content, f"En dash found in {field}"


def test_quality_review_blocks_unsupported_claims():
    """Verify that quality audit blocks applications containing fabricated claims."""
    client = TestClient(app)

    # 1. First generate an application
    response = client.post("/api/jobs/job-1/generate-application", json={"profile_id": "cs-prof-1"})
    assert response.status_code == 200
    pkg_id = response.json()["id"]

    # 2. Tamper application to include fabricated metric and company
    tampered_cover_letter = (
        "Dear Acme SaaS, I previously worked at Google as VP of Operations and grew revenue by 850% with an AI tool."
    )
    client.put(f"/api/applications/{pkg_id}", json={"cover_letter": tampered_cover_letter})

    # 3. Run Quality Review
    review_resp = client.post(f"/api/applications/{pkg_id}/quality-review")
    assert review_resp.status_code == 200
    rev_data = review_resp.json()

    assert rev_data["status"] == "BLOCKED"
    assert rev_data["quality_review"]["passed"] is False
    assert len(rev_data["quality_review"]["blocked_reasons"]) > 0
    # Must flag unsupported claim or AI mention
    summary = rev_data["quality_review"]["summary"]
    assert "BLOCKED" in summary or "unsupported" in str(rev_data["quality_review"]["blocked_reasons"]).lower()


def test_quality_review_passes_clean_application():
    """Verify clean, truthful application can achieve READY status."""
    db = TestingSessionLocal()
    job = db.query(Job).filter(Job.id == "job-1").first()
    profile = db.query(CareerProfile).filter(CareerProfile.id == "cs-prof-1").first()
    experiences = db.query(ExperienceRecord).filter(ExperienceRecord.profile_id == profile.id).all()

    # Create clean package
    clean_package = ApplicationPackage(
        id="clean-pkg-1",
        job_id="job-1",
        profile_id="cs-prof-1",
        tailored_resume_markdown="# Professional Resume\nSenior Customer Support Specialist at HelpDesk Global.",
        cover_letter="Dear Acme SaaS Hiring Team,\nI am writing to express my interest in the Customer Success Manager position at Acme SaaS.",
        short_proposal="Proposal for Customer Success at Acme SaaS.",
        recruiter_message="Hi, I noticed the opening at Acme SaaS and would like to connect.",
        application_answers=[{"question": "Why Acme SaaS?", "answer": "Strong product alignment."}],
        portfolio_selection=[],
        status="DRAFT",
    )
    db.add(clean_package)
    db.commit()

    audit = rule_based_quality_audit(clean_package, job, profile, experiences, None)
    assert audit["passed"] is True
    assert audit["status"] == "READY"
    assert len(audit["unsupported_claims"]) == 0
    assert len(audit["blocked_reasons"]) == 0
    db.close()
