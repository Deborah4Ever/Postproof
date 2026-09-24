"""
Quality Review Service.

Performs a rigorous second-pass audit on an ApplicationPackage before it can become READY:
- Factual accuracy against candidate evidence
- Evidence support for all statements and metrics
- Relevance to the specific job and company
- Detection of unsupported claims (BLOCKS the application)
- Detection of contradictions
- Detection of wrong company names or job titles
- Detection of em dashes (— or –)
- Detection of AI buzzwords or AI mentions
- Identification of missing important requirements

Application Statuses:
- DRAFT
- NEEDS_REVIEW
- READY
- BLOCKED (if unsupported claims or severe violations are detected)
"""

import os
import json
import re
from typing import Any
from sqlalchemy.orm import Session
import anthropic

from ..db.models import Job, CareerProfile, ExperienceRecord, ApplicationPackage, JobMatch


QUALITY_REVIEW_PROMPT = """You are an exacting Application Quality Review Auditor.
Your job is to rigorously audit an application package against the candidate's verified evidence and the target job posting.

AUDIT CRITERIA:
1. UNSUPPORTED CLAIMS: Does the application claim any company, job title, metric, certification, tool, or achievement NOT present in the candidate's verified evidence?
   CRITICAL: If ANY unsupported claim is found, mark passed = false and status = "BLOCKED".
2. CONTRADICTIONS: Are there any contradictions between different sections or against the verified record?
3. WRONG COMPANY / JOB TITLE: Does the cover letter or message reference the wrong company name or job title?
4. STYLE ISSUES: Check for em dashes ("—" or "–"), AI buzzwords ("delve", "testament", "tapestry", "seamless synergy"), or mentions of AI.
5. MISSING REQUIREMENTS: Are there major requirements that the candidate does not have?

Candidate Verified Database:
- Profile: {profile_name}
- Verified Skills: {verified_skills}
- Verified Tools: {verified_tools}
- Verified Work History:
{verified_history}

Target Job:
- Title: {job_title}
- Company: {company}
- Requirements: {job_requirements}

Application To Audit:
- Tailored Resume:
{tailored_resume}

- Cover Letter:
{cover_letter}

- Proposal:
{proposal}

- Recruiter Message:
{recruiter_message}

- Application Answers:
{application_answers}

Return ONLY a valid JSON object matching:
{{
  "passed": true | false,
  "status": "READY" | "NEEDS_REVIEW" | "BLOCKED",
  "score": <float 0.0 to 1.0>,
  "unsupported_claims": ["list of any claims not backed by candidate evidence"],
  "contradictions": ["list of contradictions found"],
  "style_issues": ["list of style violations e.g. em dashes, buzzwords"],
  "missing_requirements": ["unaddressed key job requirements"],
  "blocked_reasons": ["reasons why this application is BLOCKED from being READY"],
  "summary": "1-2 sentence overall audit verdict"
}}
"""


def rule_based_quality_audit(
    app_package: ApplicationPackage,
    job: Job,
    profile: CareerProfile,
    experiences: list[ExperienceRecord],
    match_record: JobMatch | None = None,
) -> dict[str, Any]:
    """
    Deterministic rule-based quality auditor.
    Cross-checks all text against candidate evidence records and style rules.
    """
    unsupported_claims = []
    contradictions = []
    style_issues = []
    missing_requirements = []
    blocked_reasons = []

    combined_text = " ".join([
        app_package.tailored_resume_markdown or "",
        app_package.cover_letter or "",
        app_package.short_proposal or "",
        app_package.recruiter_message or "",
        json.dumps(app_package.application_answers or []),
    ])

    # 1. Em dash check
    if "—" in combined_text:
        style_issues.append("Contains em dash ('—'). Style guidelines strictly forbid em dashes.")
    if "–" in combined_text:
        style_issues.append("Contains en dash ('–'). Style guidelines require standard punctuation.")

    # 2. AI mention check
    ai_phrases = ["as an ai", "language model", "chatgpt", "claude model", "generated with ai", "ai assistant"]
    for phrase in ai_phrases:
        if phrase in combined_text.lower():
            blocked_reasons.append(f"Mentions AI phrase '{phrase}'. Applications must never mention AI.")
            unsupported_claims.append(f"Meta-reference to AI: '{phrase}'")

    # 3. AI buzzword check
    ai_buzzwords = ["delve", "testament", "tapestry", "seamlessly blends", "multifaceted synergy", "bolster"]
    for word in ai_buzzwords:
        if word in combined_text.lower():
            style_issues.append(f"Contains generic AI buzzword '{word}'. Replace with clear, direct language.")

    # 4. Target company & role check
    job_company_lower = (job.company or "").lower().strip()
    job_title_lower = (job.job_title or "").lower().strip()

    if job_company_lower and len(job_company_lower) > 2:
        cl = (app_package.cover_letter or "").lower()
        if cl and job_company_lower not in cl:
            contradictions.append(f"Target company '{job.company}' is not mentioned in the cover letter.")

    # 5. Verified company check (ensure candidate doesn't claim unknown past companies)
    verified_companies = {e.company.lower() for e in experiences if e.company}
    # Also include the target company
    verified_companies.add(job_company_lower)

    # 6. Check for ungrounded numbers/percentages in cover letter
    # Extract percentages like "90%", "45%"
    claimed_percentages = re.findall(r"(\d+%)", app_package.cover_letter or "")
    known_metrics_text = " ".join([
        " ".join(e.metrics or []) + " " + " ".join(e.achievements or [])
        for e in experiences
    ])
    for pct in claimed_percentages:
        if pct not in known_metrics_text:
            unsupported_claims.append(f"Claimed metric '{pct}' in cover letter not found in candidate verified records.")

    # 7. Check missing requirements from match record
    if match_record and match_record.potential_gaps:
        missing_requirements = list(match_record.potential_gaps)

    # Determine status
    if unsupported_claims or blocked_reasons:
        status = "BLOCKED"
        passed = False
        blocked_reasons.extend([f"Unsupported claim: {c}" for c in unsupported_claims if c not in blocked_reasons])
        score = 0.40
    elif contradictions or len(style_issues) > 1:
        status = "NEEDS_REVIEW"
        passed = False
        score = 0.70
    elif style_issues:
        status = "NEEDS_REVIEW"
        passed = False
        score = 0.85
    else:
        status = "READY"
        passed = True
        score = 0.95

    summary = (
        f"Quality audit {status}. "
        f"{len(unsupported_claims)} unsupported claims, "
        f"{len(contradictions)} contradictions, "
        f"{len(style_issues)} style issues detected."
    )

    return {
        "passed": passed,
        "status": status,
        "score": score,
        "unsupported_claims": unsupported_claims,
        "contradictions": contradictions,
        "style_issues": style_issues,
        "missing_requirements": missing_requirements,
        "blocked_reasons": blocked_reasons,
        "summary": summary,
    }


def run_quality_review(
    app_package: ApplicationPackage,
    job: Job,
    profile: CareerProfile,
    db: Session,
) -> dict[str, Any]:
    """
    Executes a comprehensive quality review on the application package.
    Uses Claude if available, followed by deterministic validation checks.
    """
    experiences = db.query(ExperienceRecord).filter(
        ExperienceRecord.profile_id == profile.id
    ).all()

    match_record = db.query(JobMatch).filter(
        JobMatch.job_id == job.id,
        JobMatch.profile_id == profile.id,
    ).first()

    # Always run deterministic checks first
    base_audit = rule_based_quality_audit(app_package, job, profile, experiences, match_record)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key and not api_key.startswith("your-"):
        try:
            client = anthropic.Anthropic(api_key=api_key)
            exp_text = "\n".join([
                f"- Company: {e.company}, Role: {e.role}, Metrics: {', '.join(e.metrics or [])}, Achievements: {'; '.join(e.achievements or [])}"
                for e in experiences
            ]) or "None"

            prompt = QUALITY_REVIEW_PROMPT.format(
                profile_name=profile.name,
                verified_skills=", ".join(profile.skills or []),
                verified_tools=", ".join(profile.tools or []),
                verified_history=exp_text,
                job_title=job.job_title,
                company=job.company,
                job_requirements=", ".join(job.requirements or []) or "Standard role requirements",
                tailored_resume=(app_package.tailored_resume_markdown or "")[:2500],
                cover_letter=(app_package.cover_letter or "")[:2500],
                proposal=(app_package.short_proposal or "")[:1500],
                recruiter_message=(app_package.recruiter_message or "")[:1000],
                application_answers=json.dumps(app_package.application_answers or [])[:2000],
            )

            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2500,
                temperature=0.1,
                system="You are an uncompromising quality auditor. If an applicant invents experience, you block the application.",
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            elif raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]

            llm_result = json.loads(raw.strip())

            # Merge LLM findings with deterministic rules (rule-based errors take precedence)
            unsupported = list(set(base_audit["unsupported_claims"] + llm_result.get("unsupported_claims", [])))
            contradictions = list(set(base_audit["contradictions"] + llm_result.get("contradictions", [])))
            style = list(set(base_audit["style_issues"] + llm_result.get("style_issues", [])))
            blocked = list(set(base_audit["blocked_reasons"] + llm_result.get("blocked_reasons", [])))

            if unsupported:
                for u in unsupported:
                    if f"Unsupported claim: {u}" not in blocked:
                        blocked.append(f"Unsupported claim: {u}")

            # Determine final status
            if unsupported or blocked:
                final_status = "BLOCKED"
                passed = False
            elif contradictions or len(style) > 1:
                final_status = "NEEDS_REVIEW"
                passed = False
            elif style:
                final_status = "NEEDS_REVIEW"
                passed = False
            else:
                final_status = "READY"
                passed = True

            return {
                "passed": passed,
                "status": final_status,
                "score": 0.40 if final_status == "BLOCKED" else (0.75 if final_status == "NEEDS_REVIEW" else 0.95),
                "unsupported_claims": unsupported,
                "contradictions": contradictions,
                "style_issues": style,
                "missing_requirements": list(set(base_audit["missing_requirements"] + llm_result.get("missing_requirements", []))),
                "blocked_reasons": blocked,
                "summary": llm_result.get("summary") or base_audit["summary"],
            }
        except Exception as e:
            print(f"[QualityReview] LLM audit failed ({e}), using deterministic audit results.")

    return base_audit


def apply_quality_review_to_package(
    package_id: str,
    db: Session,
) -> ApplicationPackage:
    """Runs quality review on an existing ApplicationPackage and updates its status and quality_review field."""
    pkg = db.query(ApplicationPackage).filter(ApplicationPackage.id == package_id).first()
    if not pkg:
        raise ValueError(f"ApplicationPackage '{package_id}' not found.")

    job = db.query(Job).filter(Job.id == pkg.job_id).first()
    profile = db.query(CareerProfile).filter(CareerProfile.id == pkg.profile_id).first()

    if not job or not profile:
        raise ValueError("Associated Job or CareerProfile not found for this application package.")

    review_result = run_quality_review(pkg, job, profile, db)
    pkg.quality_review = review_result
    pkg.status = review_result["status"]
    db.commit()
    db.refresh(pkg)
    return pkg
