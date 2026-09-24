from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class PortfolioLink(BaseModel):
    label: str = Field(..., description="Label or platform, e.g. 'GitHub', 'Live Case Study'")
    url: str = Field(..., description="Valid link URL")


class EducationEntry(BaseModel):
    institution: str
    degree: str
    year: str | None = None
    details: str | None = None


class EvidenceEntry(BaseModel):
    title: str = Field(..., description="Brief title of evidence, e.g. 'Case study', 'GitHub commit', 'Live dashboard'")
    url: str | None = None
    quote: str | None = None
    artifact_type: str | None = Field(default="link", description="'link', 'document', 'metric_report', 'quote'")


# --- Experience Schemas ---
class ExperienceBase(BaseModel):
    company: str
    role: str
    dates: str
    start_date: str | None = None
    end_date: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any] | str] = Field(default_factory=list)


class ExperienceCreate(ExperienceBase):
    profile_id: str
    resume_id: str | None = None


class ExperienceUpdate(BaseModel):
    company: str | None = None
    role: str | None = None
    dates: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    responsibilities: list[str] | None = None
    achievements: list[str] | None = None
    metrics: list[str] | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    evidence: list[dict[str, Any] | str] | None = None


class ExperienceResponse(ExperienceBase):
    id: str
    profile_id: str
    resume_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Resume Schemas ---
class ResumeResponse(BaseModel):
    id: str
    profile_id: str | None = None
    filename: str
    file_type: str
    file_size: int
    raw_text: str
    is_primary: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ResumeUpdate(BaseModel):
    profile_id: str | None = None
    is_primary: bool | None = None
    raw_text: str | None = None


# --- Profile Schemas ---
class CareerProfileBase(BaseModel):
    name: str
    slug: str | None = None
    target_roles: list[str] = Field(default_factory=list)
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    certifications: list[str | dict[str, Any]] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    portfolio_links: list[dict[str, Any]] = Field(default_factory=list)
    education: list[dict[str, Any]] = Field(default_factory=list)


class CareerProfileCreate(CareerProfileBase):
    pass


class CareerProfileUpdate(BaseModel):
    name: str | None = None
    target_roles: list[str] | None = None
    summary: str | None = None
    skills: list[str] | None = None
    tools: list[str] | None = None
    certifications: list[str | dict[str, Any]] | None = None
    achievements: list[str] | None = None
    portfolio_links: list[dict[str, Any]] | None = None
    education: list[dict[str, Any]] | None = None


class CareerProfileDetail(CareerProfileBase):
    id: str
    slug: str
    created_at: datetime
    updated_at: datetime
    resumes: list[ResumeResponse] = Field(default_factory=list)
    experiences: list[ExperienceResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ExtractionResult(BaseModel):
    summary: str = ""
    target_roles: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    certifications: list[str | dict[str, Any]] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    portfolio_links: list[dict[str, Any]] = Field(default_factory=list)
    education: list[dict[str, Any]] = Field(default_factory=list)
    experiences: list[ExperienceBase] = Field(default_factory=list)


# --- Job Schemas ---
class JobBase(BaseModel):
    source: str
    source_job_id: str | None = None
    job_url: str
    company: str
    job_title: str
    description: str = ""
    location: str = "Remote"
    country: str = ""
    remote_status: str = "Remote"
    employment_type: str = "Full-time"
    salary: str | None = None
    posted_at: datetime | None = None
    closing_at: datetime | None = None
    requirements: list[str] = Field(default_factory=list)
    preferred_requirements: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    detected_category: str = "General"
    status: str = "UNVERIFIED"  # VERIFIED, UNVERIFIED, REJECTED
    verification_details: dict[str, Any] = Field(default_factory=dict)


class JobCreate(JobBase):
    pass


class JobUpdate(BaseModel):
    job_title: str | None = None
    company: str | None = None
    description: str | None = None
    location: str | None = None
    remote_status: str | None = None
    employment_type: str | None = None
    salary: str | None = None
    requirements: list[str] | None = None
    skills: list[str] | None = None
    detected_category: str | None = None
    status: str | None = None
    verification_details: dict[str, Any] | None = None


class JobResponse(JobBase):
    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobVerificationCheck(BaseModel):
    check_name: str
    passed: bool
    score: float
    details: str
    indicators: list[str] = Field(default_factory=list)


class JobVerificationResult(BaseModel):
    job_id: str
    previous_status: str
    new_status: str
    confidence: float
    checks: dict[str, Any]
    summary: str
    verified_at: datetime


# --- Matching & Evaluation Schemas ---
class CriteriaEvaluationItem(BaseModel):
    criterion_name: str
    requirement: str
    evidence: str
    status: str  # MATCH, PARTIAL MATCH, MISSING, UNKNOWN
    reason: str


class JobMatchResponse(BaseModel):
    id: str
    job_id: str
    profile_id: str
    recommended_profile_id: str | None = None
    recommended_profile_name: str | None = None
    fit_score: float
    overall_fit_verdict: str
    criteria_evaluations: dict[str, Any]  # 10 criteria details
    supporting_evidence: list[str] = Field(default_factory=list)
    potential_gaps: list[str] = Field(default_factory=list)
    summary: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GenerateApplicationRequest(BaseModel):
    profile_id: str | None = None  # None = use recommended
    include_proposal: bool = True
    include_recruiter_message: bool = True
    custom_instructions: str | None = None


class QualityReviewResult(BaseModel):
    passed: bool
    status: str  # READY, NEEDS_REVIEW, BLOCKED
    score: float
    unsupported_claims: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    style_issues: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    summary: str


class ApplicationPackageResponse(BaseModel):
    id: str
    job_id: str
    profile_id: str
    resume_id: str | None = None
    tailored_resume_markdown: str
    cover_letter: str
    short_proposal: str
    recruiter_message: str
    application_answers: list[dict[str, Any]] = Field(default_factory=list)
    portfolio_selection: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    quality_review: dict[str, Any] = Field(default_factory=dict)
    user_notes: str = ""
    applied_at: datetime | None = None
    follow_up_date: datetime | None = None
    outcome: str | None = "PENDING"
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplicationPackageUpdate(BaseModel):
    tailored_resume_markdown: str | None = None
    cover_letter: str | None = None
    short_proposal: str | None = None
    recruiter_message: str | None = None
    application_answers: list[dict[str, Any]] | None = None
    portfolio_selection: list[dict[str, Any]] | None = None
    user_notes: str | None = None
    status: str | None = None
    applied_at: datetime | None = None
    follow_up_date: datetime | None = None
    outcome: str | None = None


class ReviewQueueItemResponse(BaseModel):
    id: str
    job_id: str
    job_title: str
    company: str
    location: str
    remote_status: str
    source: str
    job_url: str
    profile_id: str
    profile_name: str
    why_it_matches: str
    potential_gaps: list[str] = Field(default_factory=list)
    resume_markdown: str
    cover_letter: str
    short_proposal: str
    recruiter_message: str
    application_answers: list[dict[str, Any]] = Field(default_factory=list)
    portfolio_selection: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    quality_review: dict[str, Any] = Field(default_factory=dict)
    user_notes: str = ""
    applied_at: datetime | None = None
    follow_up_date: datetime | None = None
    outcome: str | None = "PENDING"
    created_at: datetime
    updated_at: datetime


class FilterSettingsBase(BaseModel):
    target_countries: list[str] = Field(default_factory=list)
    target_job_titles: list[str] = Field(default_factory=list)
    target_categories: list[str] = Field(default_factory=list)
    remote_types: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    min_salary: str = ""
    max_job_age_days: int = 14
    min_relevance_score: float = 0.60
    excluded_companies: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    scan_interval_minutes: int = 30
    auto_prep_promising: bool = True
    notification_email: str = ""
    webhook_url: str = ""


class FilterSettingsUpdate(BaseModel):
    target_countries: list[str] | None = None
    target_job_titles: list[str] | None = None
    target_categories: list[str] | None = None
    remote_types: list[str] | None = None
    employment_types: list[str] | None = None
    min_salary: str | None = None
    max_job_age_days: int | None = None
    min_relevance_score: float | None = None
    excluded_companies: list[str] | None = None
    excluded_keywords: list[str] | None = None
    scan_interval_minutes: int | None = None
    auto_prep_promising: bool | None = None
    notification_email: str | None = None
    webhook_url: str | None = None


class FilterSettingsResponse(FilterSettingsBase):
    id: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationResponse(BaseModel):
    id: str
    type: str
    title: str
    message: str
    link_url: str
    read: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CostEventResponse(BaseModel):
    id: str
    service: str
    operation: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DashboardMetricsResponse(BaseModel):
    total_jobs: int
    new_jobs_24h: int
    verified_jobs: int
    qualified_jobs: int
    applications_draft: int
    applications_ready: int
    applications_approved: int
    applications_submitted: int
    interviews: int
    follow_ups: int
    total_cost_usd: float
    monid_spend_usd: float
    ai_spend_usd: float
    last_scan_at: datetime | None = None

