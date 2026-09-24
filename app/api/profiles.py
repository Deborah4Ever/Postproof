from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..db.schemas import (
    CareerProfileDetail,
    CareerProfileCreate,
    CareerProfileUpdate,
    ResumeResponse,
    ResumeUpdate,
    ExperienceResponse,
    ExperienceCreate,
    ExperienceUpdate,
    ExtractionResult,
)
from ..services import profile_service
from ..services.resume_parser import extract_structured_resume_data

router = APIRouter(prefix="/api", tags=["Candidate Profiles & Resumes"])


# --- Career Profiles ---
@router.get("/profiles", response_model=list[CareerProfileDetail])
def get_all_profiles(db: Session = Depends(get_db)):
    """List all candidate career profiles."""
    return profile_service.list_profiles(db)


@router.post("/profiles", response_model=CareerProfileDetail, status_code=201)
def create_career_profile(profile_in: CareerProfileCreate, db: Session = Depends(get_db)):
    """Create a new candidate career profile."""
    try:
        return profile_service.create_profile(db, profile_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/profiles/{profile_id}", response_model=CareerProfileDetail)
def get_career_profile(profile_id: str, db: Session = Depends(get_db)):
    """Get full career profile with attached resumes and structured experience records."""
    profile = profile_service.get_profile(db, profile_id)
    if not profile:
        # Check by slug
        profile = profile_service.get_profile_by_slug(db, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Career profile not found.")
    return profile


@router.put("/profiles/{profile_id}", response_model=CareerProfileDetail)
def update_career_profile(profile_id: str, profile_in: CareerProfileUpdate, db: Session = Depends(get_db)):
    """Edit an existing career profile."""
    updated = profile_service.update_profile(db, profile_id, profile_in)
    if not updated:
        raise HTTPException(status_code=404, detail="Career profile not found.")
    return updated


@router.delete("/profiles/{profile_id}", status_code=204)
def delete_career_profile(profile_id: str, db: Session = Depends(get_db)):
    """Delete a career profile."""
    success = profile_service.delete_profile(db, profile_id)
    if not success:
        raise HTTPException(status_code=404, detail="Career profile not found.")
    return Response(status_code=204)


# --- Resumes ---
@router.get("/resumes", response_model=list[ResumeResponse])
def get_resumes(profile_id: str | None = None, db: Session = Depends(get_db)):
    """List uploaded resumes, optionally filtered by profile_id."""
    return profile_service.list_resumes(db, profile_id)


@router.get("/resumes/{resume_id}", response_model=ResumeResponse)
def get_resume(resume_id: str, db: Session = Depends(get_db)):
    """Get resume metadata and full extracted raw text."""
    resume = profile_service.get_resume(db, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found.")
    return resume


@router.get("/resumes/{resume_id}/download")
def download_resume(resume_id: str, db: Session = Depends(get_db)):
    """Serve the original uploaded resume source document without alteration."""
    resume = profile_service.get_resume(db, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found.")
    return FileResponse(
        path=resume.file_path,
        filename=resume.filename,
        media_type="application/octet-stream",
    )


@router.post("/resumes/upload", response_model=dict, status_code=201)
async def upload_resume(
    file: UploadFile = File(...),
    profile_id: str | None = Form(None),
    replace_resume_id: str | None = Form(None),
    auto_extract: bool = Form(True),
    db: Session = Depends(get_db),
):
    """
    Upload a resume file (.pdf, .docx, .txt, .md), store it safely,
    extract raw text, and populate structured evidence if linked to a profile.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename cannot be empty.")

    try:
        resume, extracted = profile_service.save_and_extract_resume(
            db=db,
            file_obj=file.file,
            filename=file.filename,
            profile_id=profile_id,
            replace_resume_id=replace_resume_id,
            auto_extract_structure=auto_extract,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process resume: {e}")

    return {
        "message": "Resume uploaded successfully.",
        "resume": ResumeResponse.model_validate(resume),
        "extracted_data": extracted,
    }


@router.post("/resumes/{resume_id}/extract", response_model=ExtractionResult)
def re_extract_resume_data(resume_id: str, db: Session = Depends(get_db)):
    """Re-run structured extraction on an existing resume's stored raw text."""
    resume = profile_service.get_resume(db, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found.")
    if not resume.raw_text:
        raise HTTPException(status_code=400, detail="Resume contains no extractable text.")

    data = extract_structured_resume_data(resume.raw_text)
    return ExtractionResult(**data)


@router.delete("/resumes/{resume_id}", status_code=204)
def delete_resume(resume_id: str, db: Session = Depends(get_db)):
    """Delete a resume record."""
    success = profile_service.delete_resume(db, resume_id)
    if not success:
        raise HTTPException(status_code=404, detail="Resume not found.")
    return Response(status_code=204)


# --- Structured Experience & Evidence Records ---
@router.get("/experiences", response_model=list[ExperienceResponse])
def get_experiences(profile_id: str, db: Session = Depends(get_db)):
    """List all structured experience and evidence records for a profile."""
    return profile_service.list_experiences(db, profile_id)


@router.post("/experiences", response_model=ExperienceResponse, status_code=201)
def create_experience_record(exp_in: ExperienceCreate, db: Session = Depends(get_db)):
    """
    Create a structured experience record with evidence, metrics, tools, and responsibilities.
    Enforces truthfulness without hallucinated or fabricated metrics.
    """
    try:
        return profile_service.create_experience(db, exp_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/experiences/{experience_id}", response_model=ExperienceResponse)
def get_experience_record(experience_id: str, db: Session = Depends(get_db)):
    """Get single structured experience record."""
    exp = profile_service.get_experience(db, experience_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Experience record not found.")
    return exp


@router.put("/experiences/{experience_id}", response_model=ExperienceResponse)
def update_experience_record(experience_id: str, exp_in: ExperienceUpdate, db: Session = Depends(get_db)):
    """Edit experience record, updating responsibilities, metrics, tools, skills, or evidence."""
    updated = profile_service.update_experience(db, experience_id, exp_in)
    if not updated:
        raise HTTPException(status_code=404, detail="Experience record not found.")
    return updated


@router.delete("/experiences/{experience_id}", status_code=204)
def delete_experience_record(experience_id: str, db: Session = Depends(get_db)):
    """Delete an experience record."""
    success = profile_service.delete_experience(db, experience_id)
    if not success:
        raise HTTPException(status_code=404, detail="Experience record not found.")
    return Response(status_code=204)
