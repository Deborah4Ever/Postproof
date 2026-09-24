import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Text,
    Boolean,
    Integer,
    Float,
    DateTime,
    ForeignKey,
    JSON,
)
from sqlalchemy.orm import relationship
from .session import Base


def utc_now():
    return datetime.now(timezone.utc)


class CareerProfile(Base):
    __tablename__ = "career_profiles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False, unique=True, index=True)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    target_roles = Column(JSON, default=list)  # list[str]
    summary = Column(Text, default="")
    skills = Column(JSON, default=list)  # list[str]
    tools = Column(JSON, default=list)  # list[str]
    certifications = Column(JSON, default=list)  # list[str or dict]
    achievements = Column(JSON, default=list)  # list[str]
    portfolio_links = Column(JSON, default=list)  # list[dict(label, url)]
    education = Column(JSON, default=list)  # list[dict(institution, degree, year, details)]
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    resumes = relationship("Resume", back_populates="career_profile", cascade="all, delete-orphan")
    experiences = relationship("ExperienceRecord", back_populates="career_profile", cascade="all, delete-orphan")


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    profile_id = Column(String(36), ForeignKey("career_profiles.id", ondelete="SET NULL"), nullable=True, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_type = Column(String(100), nullable=False)
    file_size = Column(Integer, default=0)
    raw_text = Column(Text, nullable=False, default="")
    is_primary = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    career_profile = relationship("CareerProfile", back_populates="resumes")
    experiences = relationship("ExperienceRecord", back_populates="resume")


class ExperienceRecord(Base):
    __tablename__ = "experience_records"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    profile_id = Column(String(36), ForeignKey("career_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    resume_id = Column(String(36), ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True, index=True)

    company = Column(String(255), nullable=False)
    role = Column(String(255), nullable=False)
    dates = Column(String(150), nullable=False)
    start_date = Column(String(50), nullable=True)
    end_date = Column(String(50), nullable=True)

    responsibilities = Column(JSON, default=list)  # list[str]
    achievements = Column(JSON, default=list)  # list[str]
    metrics = Column(JSON, default=list)  # list[str]
    tools = Column(JSON, default=list)  # list[str]
    skills = Column(JSON, default=list)  # list[str]
    evidence = Column(JSON, default=list)  # list[dict or str]

    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    career_profile = relationship("CareerProfile", back_populates="experiences")
    resume = relationship("Resume", back_populates="experiences")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source = Column(String(100), nullable=False, index=True)
    source_job_id = Column(String(255), nullable=True, index=True)
    job_url = Column(Text, nullable=False)
    normalized_url = Column(String(500), nullable=True, index=True)
    company = Column(String(255), nullable=False, index=True)
    job_title = Column(String(255), nullable=False, index=True)
    description = Column(Text, default="")
    location = Column(String(255), default="Remote")
    country = Column(String(100), default="")
    remote_status = Column(String(50), default="Remote")  # Remote, Hybrid, On-site
    employment_type = Column(String(50), default="Full-time")  # Full-time, Part-time, Contract, Internship
    salary = Column(String(150), nullable=True)
    posted_at = Column(DateTime(timezone=True), nullable=True)
    closing_at = Column(DateTime(timezone=True), nullable=True)

    requirements = Column(JSON, default=list)  # list[str]
    preferred_requirements = Column(JSON, default=list)  # list[str]
    skills = Column(JSON, default=list)  # list[str]
    detected_category = Column(String(100), index=True, default="General")

    # Status: VERIFIED, UNVERIFIED, REJECTED
    status = Column(String(50), index=True, default="UNVERIFIED")
    verification_details = Column(JSON, default=dict)  # structured breakdown of 8 verification checks

    dedup_key = Column(String(255), nullable=True, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class JobMatch(Base):
    __tablename__ = "job_matches"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id = Column(String(36), ForeignKey("career_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    recommended_profile_id = Column(String(36), ForeignKey("career_profiles.id", ondelete="SET NULL"), nullable=True)

    fit_score = Column(Float, default=0.0)  # 0.0 to 1.0
    overall_fit_verdict = Column(String(50), default="MODERATE FIT")  # STRONG FIT, MODERATE FIT, WEAK FIT
    criteria_evaluations = Column(JSON, default=dict)  # Evaluations across 10 criteria
    supporting_evidence = Column(JSON, default=list)  # list[str]
    potential_gaps = Column(JSON, default=list)  # list[str]
    summary = Column(Text, default="")

    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    job = relationship("Job")
    profile = relationship("CareerProfile", foreign_keys=[profile_id])
    recommended_profile = relationship("CareerProfile", foreign_keys=[recommended_profile_id])


class ApplicationPackage(Base):
    __tablename__ = "application_packages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id = Column(String(36), ForeignKey("career_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    resume_id = Column(String(36), ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True)

    tailored_resume_markdown = Column(Text, default="")
    cover_letter = Column(Text, default="")
    short_proposal = Column(Text, default="")
    recruiter_message = Column(Text, default="")
    application_answers = Column(JSON, default=list)  # list[dict]
    portfolio_selection = Column(JSON, default=list)  # list[dict]

    # Statuses: DISCOVERED, VERIFIED, QUALIFIED, DRAFT, READY_FOR_REVIEW, APPROVED, APPLIED, REJECTED, INTERVIEW, OFFER, CLOSED
    status = Column(String(50), default="DRAFT", index=True)
    quality_review = Column(JSON, default=dict)
    user_notes = Column(Text, default="")
    applied_at = Column(DateTime(timezone=True), nullable=True)
    follow_up_date = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String(100), default="PENDING")  # PENDING, INTERVIEW_SCHEDULED, REJECTED, OFFER, NO_REPLY

    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    job = relationship("Job")
    profile = relationship("CareerProfile")
    resume = relationship("Resume")


class FilterSettings(Base):
    __tablename__ = "filter_settings"

    id = Column(String(50), primary_key=True, default="default")
    target_countries = Column(JSON, default=lambda: ["USA", "UK", "Europe", "Australia", "New Zealand"])
    target_job_titles = Column(JSON, default=list)
    target_categories = Column(JSON, default=lambda: [
        "SEO", "SEO Content", "Digital Marketing", "Social Media Marketing", "Social Media Management",
        "Customer Support", "Customer Success", "Customer Experience", "Community Management",
        "Web3", "Crypto", "Community Operations", "Operations"
    ])
    remote_types = Column(JSON, default=lambda: ["Remote", "Hybrid"])
    employment_types = Column(JSON, default=lambda: ["Full-time", "Contract"])
    min_salary = Column(String(50), default="")
    max_job_age_days = Column(Integer, default=14)
    min_relevance_score = Column(Float, default=0.60)
    excluded_companies = Column(JSON, default=list)
    excluded_keywords = Column(JSON, default=lambda: [
        "unpaid", "commission only", "security clearance required", "us citizenship required",
        "active secret clearance", "volunteer", "equity only"
    ])
    scan_interval_minutes = Column(Integer, default=30)
    auto_prep_promising = Column(Boolean, default=True)
    notification_email = Column(String(200), default="")
    webhook_url = Column(String(500), default="")
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class InAppNotification(Base):
    __tablename__ = "in_app_notifications"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    type = Column(String(50), default="APPLICATION_READY")  # APPLICATION_READY, JOB_QUALIFIED, SCAN_COMPLETED, SYSTEM_ALERT
    title = Column(String(200), nullable=False)
    message = Column(Text, default="")
    link_url = Column(String(500), default="")
    read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class CostEvent(Base):
    __tablename__ = "cost_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    service = Column(String(50), default="llm")  # "llm", "monid"
    operation = Column(String(100), default="")  # "application_generation", "match_evaluation", "quality_review", "monid_enrichment"
    tokens_in = Column(Integer, default=0)
    tokens_out = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), default=utc_now)

