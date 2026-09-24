"""
Job Verification Engine.

Implements rigorous multi-point verification for discovered jobs:
1. Valid URL Check
2. Identifiable Company Check
3. Duplicate Status Check
4. Job Availability Check
5. Suspicious / Ghost Indicators Check
6. Location Compatibility Check
7. Employment Type Check
8. Job Age & Freshness Check

Assigns one of three clear statuses:
- VERIFIED: All critical checks passed with high confidence.
- UNVERIFIED: Missing certain verification signals, aging, or pending review.
- REJECTED: Failed critical checks (invalid URL, closed/expired, scam indicators, incompatible location).
"""

import re
import urllib.parse
from datetime import datetime, timezone
import httpx
from sqlalchemy.orm import Session
from ..db.models import Job

TARGET_COUNTRIES = [
    "united states", "usa", "us",
    "united kingdom", "uk", "great britain",
    "europe", "eu", "germany", "france", "netherlands", "spain", "ireland", "sweden", "switzerland",
    "australia", "au",
    "new zealand", "nz",
    "canada", "worldwide", "anywhere", "global", "remote"
]

SUSPICIOUS_PHRASES = [
    "send payment",
    "wire transfer",
    "western union",
    "cashier's check",
    "deposit required",
    "no interview necessary",
    "telegram only",
    "whatsapp only to apply",
    "crypto transfer required",
    "earn $5000 weekly with no experience",
    "pay upfront",
    "buy equipment from our vendor",
]

GENERIC_COMPANY_NAMES = [
    "unknown",
    "confidential",
    "stealth",
    "stealth startup",
    "n/a",
    "none",
    "various",
    "private client",
    "hiring company",
]


def verify_job_record(job: Job, db: Session, check_network_live: bool = True) -> dict:
    """
    Executes all 8 verification checks against a job record.
    Updates job.status and job.verification_details in the database.
    """
    checks = {}
    flags = []
    points = 0
    max_points = 8

    # 1. Valid URL Check
    url_valid = False
    url_details = "Valid HTTP/HTTPS URL"
    if job.job_url:
        try:
            parsed = urllib.parse.urlparse(job.job_url)
            if parsed.scheme in ("http", "https") and len(parsed.netloc) > 3 and "." in parsed.netloc:
                url_valid = True
            else:
                url_details = "Invalid scheme or host"
        except Exception:
            url_details = "URL parsing failed"
    else:
        url_details = "Missing URL"

    checks["valid_url"] = {
        "passed": url_valid,
        "score": 1.0 if url_valid else 0.0,
        "details": url_details,
    }
    if url_valid:
        points += 1
    else:
        flags.append("invalid_url")

    # 2. Identifiable Company Check
    comp_clean = (job.company or "").strip().lower()
    company_identifiable = len(comp_clean) >= 2 and comp_clean not in GENERIC_COMPANY_NAMES
    checks["identifiable_company"] = {
        "passed": company_identifiable,
        "score": 1.0 if company_identifiable else 0.0,
        "details": f"Company: '{job.company}'" if company_identifiable else "Generic or undisclosed company",
    }
    if company_identifiable:
        points += 1
    else:
        flags.append("generic_company")

    # 3. Duplicate Status Check
    existing_duplicates = db.query(Job).filter(
        Job.id != job.id,
        Job.company == job.company,
        Job.job_title == job.job_title,
    ).count()

    is_not_duplicate = (existing_duplicates == 0)
    checks["duplicate_status"] = {
        "passed": is_not_duplicate,
        "score": 1.0 if is_not_duplicate else 0.0,
        "details": "Unique job posting" if is_not_duplicate else f"Found {existing_duplicates} potential duplicates",
    }
    if is_not_duplicate:
        points += 1

    # 4. Job Availability Check
    availability_passed = True
    availability_details = "Posting active"
    if check_network_live and url_valid:
        try:
            with httpx.Client(timeout=6.0, follow_redirects=True, headers={"User-Agent": "JobAgent/1.0"}) as client:
                resp = client.head(job.job_url)
                if resp.status_code in (404, 410):
                    availability_passed = False
                    availability_details = f"HTTP {resp.status_code} - Page closed or expired"
                elif resp.status_code >= 400:
                    # Retry with GET in case HEAD was refused by Cloudflare
                    get_resp = client.get(job.job_url)
                    if get_resp.status_code in (404, 410):
                        availability_passed = False
                        availability_details = f"HTTP {get_resp.status_code} - Posting is closed"
                    elif "job is no longer available" in get_resp.text.lower() or "this job has expired" in get_resp.text.lower():
                        availability_passed = False
                        availability_details = "Page indicates posting is closed or expired"
        except Exception:
            # Network timeout / anti-bot on head is non-fatal for availability
            availability_details = "Live check skipped (domain protected or timeout)"

    checks["job_availability"] = {
        "passed": availability_passed,
        "score": 1.0 if availability_passed else 0.0,
        "details": availability_details,
    }
    if availability_passed:
        points += 1
    else:
        flags.append("job_expired")

    # 5. Suspicious Indicators Check
    full_text = f"{job.job_title} {job.description}".lower()
    suspicious_found = [phrase for phrase in SUSPICIOUS_PHRASES if phrase in full_text]
    if len(job.description or "") < 40 and not job.requirements:
        suspicious_found.append("Extremely empty description (< 40 characters)")

    is_clean = len(suspicious_found) == 0
    checks["suspicious_indicators"] = {
        "passed": is_clean,
        "score": 1.0 if is_clean else 0.0,
        "details": "No suspicious fraud or scam patterns found" if is_clean else f"Flagged indicators: {', '.join(suspicious_found)}",
        "indicators": suspicious_found,
    }
    if is_clean:
        points += 1
    else:
        flags.append("suspicious_patterns")

    # 6. Location Compatibility Check
    loc_combined = f"{job.location} {job.country}".lower()
    location_compatible = any(target in loc_combined for target in TARGET_COUNTRIES) or (not loc_combined.strip())
    checks["location_compatibility"] = {
        "passed": location_compatible,
        "score": 1.0 if location_compatible else 0.0,
        "details": f"Location '{job.location}' permits target regions (USA/UK/EU/AU/NZ/Worldwide)" if location_compatible else f"Location '{job.location}' outside target regions",
    }
    if location_compatible:
        points += 1
    else:
        flags.append("incompatible_location")

    # 7. Employment Type Check
    emp_type = (job.employment_type or "").strip().lower()
    emp_valid = any(t in emp_type for t in ["full-time", "part-time", "contract", "permanent", "freelance", "internship"]) or not emp_type
    checks["employment_type"] = {
        "passed": emp_valid,
        "score": 1.0 if emp_valid else 0.5,
        "details": f"Standard type: {job.employment_type}" if emp_valid else f"Non-standard type: {job.employment_type}",
    }
    if emp_valid:
        points += 1

    # 8. Job Age & Freshness Check
    age_score = 1.0
    age_details = "Fresh posting"
    if job.posted_at:
        now = datetime.now(timezone.utc)
        posted = job.posted_at if job.posted_at.tzinfo else job.posted_at.replace(tzinfo=timezone.utc)
        days_old = (now - posted).days
        if days_old <= 14:
            age_score = 1.0
            age_details = f"Posted {days_old} days ago (very fresh)"
        elif days_old <= 30:
            age_score = 0.8
            age_details = f"Posted {days_old} days ago (fresh)"
        elif days_old <= 60:
            age_score = 0.5
            age_details = f"Posted {days_old} days ago (moderately aged)"
        else:
            age_score = 0.2
            age_details = f"Posted {days_old} days ago (aging, higher ghost-job probability)"
            flags.append("stale_posting")

    checks["job_age"] = {
        "passed": age_score >= 0.5,
        "score": age_score,
        "details": age_details,
    }
    if age_score >= 0.5:
        points += 1

    # ------------------------------------------------------------------
    # Verdict Determination
    # ------------------------------------------------------------------
    confidence = round(points / max_points, 2)

    if not url_valid or not availability_passed or "suspicious_patterns" in flags:
        final_status = "REJECTED"
        summary = "Rejected: Failed core legitimacy or availability checks."
    elif not location_compatible:
        final_status = "REJECTED"
        summary = "Rejected: Location outside target regions (USA, UK, Europe, Australia, New Zealand)."
    elif not company_identifiable:
        final_status = "UNVERIFIED"
        summary = "Unverified: Company identity is generic or undisclosed."
    elif age_score < 0.5:
        final_status = "UNVERIFIED"
        summary = "Unverified: Posting is older than 60 days, requiring manual verification."
    elif confidence >= 0.75:
        final_status = "VERIFIED"
        summary = "Verified: Valid URL, identifiable company, clean legitimacy signals, active status."
    else:
        final_status = "UNVERIFIED"
        summary = "Unverified: Passed baseline checks, additional review recommended."

    # Update Job model
    job.status = final_status
    job.verification_details = {
        "confidence": confidence,
        "final_status": final_status,
        "summary": summary,
        "checks": checks,
        "flags": flags,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    db.commit()
    db.refresh(job)

    return {
        "job_id": job.id,
        "status": final_status,
        "confidence": confidence,
        "summary": summary,
        "checks": checks,
    }


def verify_unverified_jobs_batch(db: Session, limit: int = 25) -> list[dict]:
    """Runs the 8-point verification engine over UNVERIFIED jobs."""
    unverified = db.query(Job).filter(Job.status == "UNVERIFIED").limit(limit).all()
    results = []
    for job in unverified:
        res = verify_job_record(job, db, check_network_live=True)
        results.append(res)
    return results
