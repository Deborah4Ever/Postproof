import os
import re
import uuid
import shutil
from typing import BinaryIO
from sqlalchemy.orm import Session
from ..db.models import CareerProfile, Resume, ExperienceRecord
from ..db.schemas import CareerProfileCreate, CareerProfileUpdate, ExperienceCreate, ExperienceUpdate
from .resume_parser import extract_raw_text_from_file, extract_structured_resume_data

UPLOAD_DIR = os.environ.get("RESUME_UPLOAD_DIR", "uploads/resumes")
os.makedirs(UPLOAD_DIR, exist_ok=True)

DEFAULT_PROFILES = [
    {
        "name": "SEO / Content / Digital Marketing",
        "slug": "seo-content-digital-marketing",
        "target_roles": ["SEO Specialist", "Content Strategist", "Content Writer", "Digital Marketing Manager", "Growth Marketer"],
        "summary": "Specialist in organic search growth, high-converting content marketing, technical SEO, and digital acquisition campaigns.",
        "skills": ["Search Engine Optimization (SEO)", "Content Strategy", "Keyword Research", "On-Page Optimization", "Technical SEO Audits", "Link Building", "Copywriting", "Editorial Calendar Management"],
        "tools": ["Google Analytics 4", "Google Search Console", "Ahrefs", "Semrush", "Screaming Frog", "WordPress", "Clearscope", "SurferSEO"],
    },
    {
        "name": "Social Media Marketing / Social Media Management",
        "slug": "social-media-marketing-management",
        "target_roles": ["Social Media Manager", "Social Media Strategist", "Community Content Creator", "Organic Social Lead"],
        "summary": "Expert in social media branding, viral campaign execution, community-first content distribution, and analytics across major networks.",
        "skills": ["Social Media Strategy", "Content Creation", "Audience Engagement", "Trend Analysis", "Copywriting", "Paid Social Advertising", "Influencer Outreach", "Analytics & Reporting"],
        "tools": ["Buffer", "Hootsuite", "Sprout Social", "Meta Business Suite", "Canva", "Figma", "CapCut", "TikTok Analytics"],
    },
    {
        "name": "Customer Support / Customer Success / Customer Experience",
        "slug": "customer-support-success-experience",
        "target_roles": ["Customer Success Manager", "Customer Support Specialist", "Customer Experience Lead", "Support Operations"],
        "summary": "High-empathy professional focused on onboarding retention, rapid problem resolution, proactive customer advocacy, and support workflows.",
        "skills": ["Customer Onboarding", "Churn Reduction", "Ticket Triage", "Escalation Management", "Knowledge Base Creation", "Customer Journey Mapping", "SLA Compliance", "Voice of Customer (VoC)"],
        "tools": ["Zendesk", "Intercom", "Salesforce Service Cloud", "HubSpot CRM", "Jira Service Management", "Gainsight", "Loom", "Notion"],
    },
    {
        "name": "Web3 / Community / Operations",
        "slug": "web3-community-operations",
        "target_roles": ["Web3 Community Manager", "Operations Manager", "DAO Coordinator", "Ecosystem Growth Specialist"],
        "summary": "Operator bridging decentralized ecosystems, governance, discord/telegram community growth, tokenomics events, and partner operations.",
        "skills": ["Community Moderation", "DAO Governance", "Ambassador Programs", "Event Production (AMAs, Spaces)", "Cross-Functional Operations", "Partnership Coordination", "Tokenomics Basics"],
        "tools": ["Discord", "Telegram", "Twitter / X Spaces", "Snapshot", "Collab.Land", "Dune Analytics", "Notion", "Trello", "Google Workspace"],
    },
]


def slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r'[/\\+&|]', '-', s)
    s = re.sub(r'[^a-z0-9\-]', '', s)
    s = re.sub(r'-+', '-', s)
    return s.strip('-')


def seed_default_profiles(db: Session):
    """Initializes the 4 foundational career profiles if the database is empty."""
    for p_data in DEFAULT_PROFILES:
        existing = db.query(CareerProfile).filter(
            (CareerProfile.slug == p_data["slug"]) | (CareerProfile.name == p_data["name"])
        ).first()
        if not existing:
            profile = CareerProfile(
                name=p_data["name"],
                slug=p_data["slug"],
                target_roles=p_data["target_roles"],
                summary=p_data["summary"],
                skills=p_data["skills"],
                tools=p_data["tools"],
                certifications=[],
                achievements=[],
                portfolio_links=[],
                education=[],
            )
            db.add(profile)
    db.commit()


# --- Profile Operations ---
def list_profiles(db: Session) -> list[CareerProfile]:
    return db.query(CareerProfile).order_by(CareerProfile.created_at.asc()).all()


def get_profile(db: Session, profile_id: str) -> CareerProfile | None:
    return db.query(CareerProfile).filter(CareerProfile.id == profile_id).first()


def get_profile_by_slug(db: Session, slug: str) -> CareerProfile | None:
    return db.query(CareerProfile).filter(CareerProfile.slug == slug).first()


def create_profile(db: Session, data: CareerProfileCreate) -> CareerProfile:
    slug = data.slug or slugify(data.name)
    # Check collision
    existing = db.query(CareerProfile).filter((CareerProfile.slug == slug) | (CareerProfile.name == data.name)).first()
    if existing:
        raise ValueError(f"Profile with name '{data.name}' or slug '{slug}' already exists.")

    profile = CareerProfile(
        name=data.name,
        slug=slug,
        target_roles=data.target_roles,
        summary=data.summary,
        skills=data.skills,
        tools=data.tools,
        certifications=data.certifications,
        achievements=data.achievements,
        portfolio_links=data.portfolio_links,
        education=data.education,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def update_profile(db: Session, profile_id: str, data: CareerProfileUpdate) -> CareerProfile | None:
    profile = get_profile(db, profile_id)
    if not profile:
        return None

    update_dict = data.model_dump(exclude_unset=True)
    if "name" in update_dict and update_dict["name"]:
        profile.name = update_dict["name"]
        profile.slug = slugify(update_dict["name"])

    for field, val in update_dict.items():
        if field != "name":
            setattr(profile, field, val)

    db.commit()
    db.refresh(profile)
    return profile


def delete_profile(db: Session, profile_id: str) -> bool:
    profile = get_profile(db, profile_id)
    if not profile:
        return False
    db.delete(profile)
    db.commit()
    return True


# --- Resume Operations ---
def list_resumes(db: Session, profile_id: str | None = None) -> list[Resume]:
    query = db.query(Resume)
    if profile_id:
        query = query.filter(Resume.profile_id == profile_id)
    return query.order_by(Resume.created_at.desc()).all()


def get_resume(db: Session, resume_id: str) -> Resume | None:
    return db.query(Resume).filter(Resume.id == resume_id).first()


def save_and_extract_resume(
    db: Session,
    file_obj: BinaryIO,
    filename: str,
    profile_id: str | None = None,
    replace_resume_id: str | None = None,
    auto_extract_structure: bool = True,
) -> tuple[Resume, dict | None]:
    """
    Saves the physical resume file, extracts raw text, associates with a profile,
    and optionally extracts structured data (skills, tools, experience, evidence).
    Never overwrites or destroys the original resume file.
    """
    resume_id = replace_resume_id or str(uuid.uuid4())
    safe_ext = os.path.splitext(filename)[1].lower()
    storage_filename = f"{resume_id}_{filename}"
    file_path = os.path.join(UPLOAD_DIR, storage_filename)

    # Save to disk
    file_obj.seek(0)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file_obj, buffer)
    file_size = os.path.getsize(file_path)

    # Extract raw text from saved file
    with open(file_path, "rb") as saved_file:
        raw_text = extract_raw_text_from_file(saved_file, filename)

    if replace_resume_id:
        resume = get_resume(db, replace_resume_id)
        if not resume:
            raise ValueError(f"Resume with id '{replace_resume_id}' not found.")
        resume.filename = filename
        resume.file_path = file_path
        resume.file_size = file_size
        resume.file_type = safe_ext
        resume.raw_text = raw_text
        if profile_id:
            resume.profile_id = profile_id
    else:
        # Mark other resumes for this profile as non-primary if this is primary
        if profile_id:
            db.query(Resume).filter(Resume.profile_id == profile_id).update({"is_primary": False})
        resume = Resume(
            id=resume_id,
            profile_id=profile_id,
            filename=filename,
            file_path=file_path,
            file_type=safe_ext,
            file_size=file_size,
            raw_text=raw_text,
            is_primary=True,
        )
        db.add(resume)

    db.commit()
    db.refresh(resume)

    extracted_data = None
    if auto_extract_structure and raw_text:
        extracted_data = extract_structured_resume_data(raw_text)

        # If profile_id is attached, merge truthful non-duplicate skills/tools and experiences
        if profile_id:
            profile = get_profile(db, profile_id)
            if profile and extracted_data:
                # Add extracted experiences with evidence layer
                for exp_data in extracted_data.get("experiences", []):
                    # Check if already present by company + role
                    exists = db.query(ExperienceRecord).filter(
                        ExperienceRecord.profile_id == profile_id,
                        ExperienceRecord.company == exp_data.get("company"),
                        ExperienceRecord.role == exp_data.get("role"),
                    ).first()
                    if not exists:
                        new_exp = ExperienceRecord(
                            profile_id=profile_id,
                            resume_id=resume.id,
                            company=exp_data.get("company", "Unknown"),
                            role=exp_data.get("role", "Unknown"),
                            dates=exp_data.get("dates", "Unknown"),
                            start_date=exp_data.get("start_date"),
                            end_date=exp_data.get("end_date"),
                            responsibilities=exp_data.get("responsibilities", []),
                            achievements=exp_data.get("achievements", []),
                            metrics=exp_data.get("metrics", []),
                            tools=exp_data.get("tools", []),
                            skills=exp_data.get("skills", []),
                            evidence=exp_data.get("evidence", []),
                        )
                        db.add(new_exp)

                # Merge extracted skills & tools into profile if not present
                current_skills = set(profile.skills or [])
                for s in extracted_data.get("skills", []):
                    if s and s not in current_skills:
                        profile.skills = (profile.skills or []) + [s]

                current_tools = set(profile.tools or [])
                for t in extracted_data.get("tools", []):
                    if t and t not in current_tools:
                        profile.tools = (profile.tools or []) + [t]

                if not profile.summary and extracted_data.get("summary"):
                    profile.summary = extracted_data["summary"]

                # Append education/certifications/portfolio if empty
                if not profile.education and extracted_data.get("education"):
                    profile.education = extracted_data["education"]
                if not profile.certifications and extracted_data.get("certifications"):
                    profile.certifications = extracted_data["certifications"]
                if not profile.portfolio_links and extracted_data.get("portfolio_links"):
                    profile.portfolio_links = extracted_data["portfolio_links"]

                db.commit()
                db.refresh(profile)

    return resume, extracted_data


def delete_resume(db: Session, resume_id: str) -> bool:
    resume = get_resume(db, resume_id)
    if not resume:
        return False
    # Keep the file on disk as a safe source document, or remove if desired
    db.delete(resume)
    db.commit()
    return True


# --- Experience & Evidence Operations ---
def list_experiences(db: Session, profile_id: str) -> list[ExperienceRecord]:
    return db.query(ExperienceRecord).filter(
        ExperienceRecord.profile_id == profile_id
    ).order_by(ExperienceRecord.created_at.desc()).all()


def get_experience(db: Session, experience_id: str) -> ExperienceRecord | None:
    return db.query(ExperienceRecord).filter(ExperienceRecord.id == experience_id).first()


def create_experience(db: Session, data: ExperienceCreate) -> ExperienceRecord:
    profile = get_profile(db, data.profile_id)
    if not profile:
        raise ValueError(f"Profile with id '{data.profile_id}' does not exist.")

    record = ExperienceRecord(
        profile_id=data.profile_id,
        resume_id=data.resume_id,
        company=data.company,
        role=data.role,
        dates=data.dates,
        start_date=data.start_date,
        end_date=data.end_date,
        responsibilities=data.responsibilities,
        achievements=data.achievements,
        metrics=data.metrics,
        tools=data.tools,
        skills=data.skills,
        evidence=data.evidence,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def update_experience(db: Session, experience_id: str, data: ExperienceUpdate) -> ExperienceRecord | None:
    record = get_experience(db, experience_id)
    if not record:
        return None

    update_dict = data.model_dump(exclude_unset=True)
    for field, val in update_dict.items():
        setattr(record, field, val)

    db.commit()
    db.refresh(record)
    return record


def delete_experience(db: Session, experience_id: str) -> bool:
    record = get_experience(db, experience_id)
    if not record:
        return False
    db.delete(record)
    db.commit()
    return True
