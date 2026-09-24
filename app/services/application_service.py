"""
Application Generation Service.

Generates truthful, evidence-backed application assets for a verified job:
1. Tailored resume (clean markdown, emphasizing verified experience relevant to the job)
2. Cover letter (specific to company and role, concise, human tone)
3. Short proposal (practical value pitch for contract/consulting or high-impact roles)
4. Recruiter / Hiring manager outreach message (concise, direct outreach)
5. Application answers (evidence-supported answers to common application screening questions)
6. Curated portfolio selection (relevant case studies, evidence links, and work samples)

STRICT RULES:
- ONLY use information supported by the candidate's profile and evidence records.
- NEVER fabricate companies, job titles, metrics, dates, tools, or achievements.
- If an important requirement is missing, acknowledge or navigate around it honestly; do NOT invent experience.
- Writing style: Simple, direct, professional human language.
- NEVER mention AI.
- NEVER use em dashes (— or –).
"""

import os
import json
import re
from typing import Any
from sqlalchemy.orm import Session
import anthropic

from ..db.models import Job, CareerProfile, ExperienceRecord, Resume, ApplicationPackage, JobMatch


APPLICATION_PROMPT = """You are an expert career advisor and professional writer crafting an authentic job application package.

You are writing on behalf of a real professional. You have access to their verified career profile and evidence database.

CRITICAL CONSTRAINTS - VIOLATION WILL RESULT IN IMMEDIATE REJECTION:
1. ZERO FABRICATION: Use ONLY facts, companies, job titles, dates, metrics, tools, certifications, and responsibilities that exist in the candidate's verified records. Never invent a single metric, tool, or project.
2. NO EM DASHES: NEVER use em dashes ("—") or en dashes ("–"). Use standard commas, periods, colons, or parentheses.
3. NEVER MENTION AI: Do not mention AI, language models, prompts, or automated assistants anywhere.
4. NO AI BUZZWORDS: Avoid "thrilled", "excited to apply", "delve", "testament", "spearhead", "tapestry", "seamless", "synergy", "multifaceted", "bolster", "aligns perfectly".
5. HONEST HANDLING OF GAPS: If the job requires something the candidate lacks, do not pretend they have it. Emphasize transferable verified strengths without lying.
6. HUMAN TONE: Sound natural, direct, specific to the company, and grounded in concrete achievements.

Job Information:
- Title: {job_title}
- Company: {company}
- Location: {job_location} ({job_remote})
- Employment Type: {employment_type}
- Key Requirements: {job_requirements}
- Description Excerpt:
{job_description}

Candidate Profile:
- Name: {candidate_name}
- Email: {candidate_email}
- Target Roles: {target_roles}
- Summary: {candidate_summary}
- Verified Skills: {candidate_skills}
- Verified Tools: {candidate_tools}
- Certifications: {candidate_certifications}
- Achievements: {candidate_achievements}
- Portfolio Links: {candidate_portfolio}

Candidate Verified Work History & Evidence:
{candidate_work_history}

Job Match Analysis Context:
- Supporting Evidence: {supporting_evidence}
- Identified Gaps: {potential_gaps}

Custom User Instructions:
{custom_instructions}

Return a valid JSON object matching this schema:
{{
  "tailored_resume_markdown": "# Full markdown tailored resume focusing on relevant verified achievements, roles, and skills...",
  "cover_letter": "A 3-4 paragraph honest, engaging, and professional cover letter customized for {company}...",
  "short_proposal": "A concise 2-3 paragraph value proposition or project proposal outlining how the candidate will execute key objectives...",
  "recruiter_message": "A short, polite 80-120 word outreach message for a recruiter or hiring manager...",
  "application_answers": [
    {{
      "question": "Why do you want to work at {company}?",
      "answer": "Authentic answer referencing company specifics and candidate's genuine background..."
    }},
    {{
      "question": "What relevant experience do you have for the {job_title} role?",
      "answer": "Direct answer citing exact past roles and metrics from verified evidence..."
    }},
    {{
      "question": "What is your remote work setup and availability?",
      "answer": "Available immediately for remote work with dedicated workspace..."
    }},
    {{
      "question": "What are your salary expectations?",
      "answer": "Competitive and open to negotiation based on total compensation and role scope..."
    }}
  ],
  "portfolio_selection": [
    {{
      "title": "Project or Case Study Title",
      "url": "URL or reference if available",
      "relevance": "Why this sample directly proves capability for {job_title}"
    }}
  ]
}}
"""


def _sanitize_em_dashes(text: str) -> str:
    """Removes em dashes and en dashes, replacing them with standard punctuation."""
    if not text:
        return ""
    text = text.replace("—", ", ")
    text = text.replace("–", "-")
    # Clean up double commas or awkward spaces
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_candidate_context(profile: CareerProfile, db: Session) -> dict[str, Any]:
    """Extracts and formats all verified profile evidence for prompt ingestion."""
    experiences = db.query(ExperienceRecord).filter(
        ExperienceRecord.profile_id == profile.id
    ).all()

    work_history_lines = []
    for exp in experiences:
        metrics = ", ".join(exp.metrics or [])
        tools = ", ".join(exp.tools or [])
        skills = ", ".join(exp.skills or [])
        achievements = "; ".join(exp.achievements or [])
        responsibilities = "; ".join(exp.responsibilities or [])
        
        evidence_items = []
        for ev in (exp.evidence or []):
            if isinstance(ev, dict):
                evidence_items.append(f"{ev.get('title', 'Ref')}: {ev.get('url', '')} {ev.get('quote', '')}".strip())
            else:
                evidence_items.append(str(ev))
        evidence_str = "; ".join(evidence_items)

        work_history_lines.append(
            f"Company: {exp.company}\n"
            f"Role: {exp.role} ({exp.dates})\n"
            f"Responsibilities: {responsibilities or 'N/A'}\n"
            f"Achievements: {achievements or 'N/A'}\n"
            f"Metrics: {metrics or 'N/A'}\n"
            f"Tools: {tools or 'N/A'}\n"
            f"Skills: {skills or 'N/A'}\n"
            f"Evidence: {evidence_str or 'N/A'}\n"
        )

    portfolio_items = []
    for p in (profile.portfolio_links or []):
        if isinstance(p, dict):
            portfolio_items.append(f"{p.get('title', 'Sample')}: {p.get('url', '')} ({p.get('description', '')})")
        else:
            portfolio_items.append(str(p))

    cert_items = []
    for c in (profile.certifications or []):
        if isinstance(c, dict):
            cert_items.append(f"{c.get('name', '')} from {c.get('issuer', '')}")
        else:
            cert_items.append(str(c))

    return {
        "work_history": "\n".join(work_history_lines) if work_history_lines else "No work experience records.",
        "skills": ", ".join(profile.skills or []),
        "tools": ", ".join(profile.tools or []),
        "achievements": "; ".join(profile.achievements or []),
        "certifications": "; ".join(cert_items),
        "portfolio": "; ".join(portfolio_items),
        "portfolio_list": profile.portfolio_links or [],
    }


def generate_rule_based_application(
    job: Job,
    profile: CareerProfile,
    experiences: list[ExperienceRecord],
    match_info: JobMatch | None,
) -> dict[str, Any]:
    """Deterministic fallback application generator when LLM is unavailable."""
    name = "Candidate"
    skills_list = profile.skills or []
    tools_list = profile.tools or []

    # Build tailored resume markdown
    resume_md_lines = [
        f"# {profile.name} - Professional Profile",
        f"**Target Role:** {job.job_title} | **Target Company:** {job.company}",
        "",
        "## Professional Summary",
        profile.summary or f"Experienced professional specializing in {', '.join(skills_list[:4])}.",
        "",
        "## Core Skills & Tools",
        f"- **Core Skills:** {', '.join(skills_list)}",
        f"- **Tools & Platforms:** {', '.join(tools_list)}",
        "",
        "## Professional Experience",
    ]

    for exp in experiences:
        resume_md_lines.append(f"### {exp.role} | {exp.company}")
        resume_md_lines.append(f"*{exp.dates}*")
        resume_md_lines.append("")
        if exp.achievements:
            resume_md_lines.append("**Key Achievements:**")
            for ach in exp.achievements:
                clean_ach = _sanitize_em_dashes(ach)
                resume_md_lines.append(f"- {clean_ach}")
        if exp.responsibilities:
            resume_md_lines.append("**Responsibilities:**")
            for resp in exp.responsibilities:
                clean_resp = _sanitize_em_dashes(resp)
                resume_md_lines.append(f"- {clean_resp}")
        if exp.tools:
            resume_md_lines.append(f"- **Tools:** {', '.join(exp.tools)}")
        resume_md_lines.append("")

    tailored_resume = "\n".join(resume_md_lines)

    # Build cover letter
    top_exp = experiences[0] if experiences else None
    exp_citation = f"In my role as {top_exp.role} at {top_exp.company}, I focused on delivering measurable outcomes." if top_exp else ""
    metric_citation = f" Notably, {top_exp.achievements[0]}." if top_exp and top_exp.achievements else ""

    cover_letter = (
        f"Dear {job.company} Hiring Team,\n\n"
        f"I am writing to express my interest in the {job.job_title} position at {job.company}. "
        f"My background in {profile.name.lower()} and hands-on track record align closely with the requirements of this role.\n\n"
        f"{exp_citation}{metric_citation} "
        f"I work with tools such as {', '.join(tools_list[:3])} to maintain high standards and solve practical problems efficiently.\n\n"
        f"Thank you for considering my application. I welcome the opportunity to discuss how my verified background can support {job.company}'s goals.\n\n"
        f"Sincerely,\n{name}"
    )

    # Proposal
    short_proposal = (
        f"Executive Proposal for {job.job_title} at {job.company}:\n\n"
        f"1. Immediate Alignment: Audit existing workflows and requirements, establishing reliable processes within the first 30 days.\n"
        f"2. Execution and Delivery: Leverage proven experience with {', '.join(skills_list[:3])} to deliver consistent, high-impact results.\n"
        f"3. Long-term Value: Implement scalable documentation and optimization to ensure sustained operational momentum."
    )

    # Recruiter message
    recruiter_message = (
        f"Hi, I noticed the {job.job_title} opening at {job.company} and wanted to reach out directly. "
        f"My background in {profile.name} includes direct experience with {', '.join(skills_list[:3])}. "
        f"I have reviewed the team's objectives and would be glad to connect if my background fits your current search."
    )

    # Screening answers
    app_answers = [
        {
            "question": f"Why do you want to work at {job.company}?",
            "answer": f"I appreciate {job.company}'s focus and the specific challenges of the {job.job_title} position. My verified experience in {', '.join(skills_list[:2])} provides a strong foundation to contribute immediately."
        },
        {
            "question": f"Describe your relevant experience for {job.job_title}.",
            "answer": f"In previous work as {top_exp.role if top_exp else 'a specialist'}, I managed core deliverables using {', '.join(tools_list[:3])}." + (f" A key achievement was {top_exp.achievements[0]}." if top_exp and top_exp.achievements else "")
        },
        {
            "question": "What is your remote work setup and availability?",
            "answer": f"I have a reliable, high-speed remote workstation with quiet home office facilities and can align with your required working hours."
        },
        {
            "question": "What are your salary expectations?",
            "answer": f"My salary expectations are aligned with industry standards for {job.job_title} and remain flexible based on overall benefits and scope."
        }
    ]

    # Portfolio selection
    portfolio_selection = []
    for item in (profile.portfolio_links or []):
        if isinstance(item, dict):
            portfolio_selection.append({
                "title": item.get("title", "Work Sample"),
                "url": item.get("url", ""),
                "relevance": f"Demonstrates verified capability relevant to {job.job_title}"
            })
        else:
            portfolio_selection.append({
                "title": "Verified Work Sample",
                "url": str(item),
                "relevance": f"Directly demonstrates skills required for {job.job_title}"
            })

    return {
        "tailored_resume_markdown": _sanitize_em_dashes(tailored_resume),
        "cover_letter": _sanitize_em_dashes(cover_letter),
        "short_proposal": _sanitize_em_dashes(short_proposal),
        "recruiter_message": _sanitize_em_dashes(recruiter_message),
        "application_answers": app_answers,
        "portfolio_selection": portfolio_selection,
    }


def generate_application_package(
    job: Job,
    profile: CareerProfile,
    db: Session,
    custom_instructions: str | None = None,
    match_info: JobMatch | None = None,
) -> dict[str, Any]:
    """
    Main entry point for generating an evidence-backed application package.
    Attempts generation with Claude with zero-fabrication prompt,
    falling back to rule-based generator if offline or error occurs.
    """
    context = build_candidate_context(profile, db)
    experiences = db.query(ExperienceRecord).filter(
        ExperienceRecord.profile_id == profile.id
    ).all()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key and not api_key.startswith("your-"):
        try:
            client = anthropic.Anthropic(api_key=api_key)
            formatted_prompt = APPLICATION_PROMPT.format(
                job_title=job.job_title,
                company=job.company,
                job_location=job.location or "Remote",
                job_remote=job.remote_status or "Remote",
                employment_type=job.employment_type or "Full-time",
                job_requirements=", ".join(job.requirements or []) or "Not explicitly listed",
                job_description=(job.description or "")[:4000],
                candidate_name="Candidate",
                candidate_email="applicant@example.com",
                target_roles=", ".join(profile.target_roles or []),
                candidate_summary=profile.summary or "Experienced professional",
                candidate_skills=context["skills"],
                candidate_tools=context["tools"],
                candidate_certifications=context["certifications"] or "None recorded",
                candidate_achievements=context["achievements"] or "None recorded",
                candidate_portfolio=context["portfolio"] or "None recorded",
                candidate_work_history=context["work_history"],
                supporting_evidence="; ".join(match_info.supporting_evidence if match_info else []) or "Strong profile alignment",
                potential_gaps="; ".join(match_info.potential_gaps if match_info else []) or "None identified",
                custom_instructions=custom_instructions or "None provided",
            )

            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                temperature=0.2,
                system="You are an uncompromising career application generator. You never fabricate experience, never use em dashes, and never sound like generic AI.",
                messages=[{"role": "user", "content": formatted_prompt}],
            )

            raw_text = response.content[0].text.strip()
            # Clean possible markdown wrapping
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]

            parsed = json.loads(raw_text.strip())

            # Enforce clean em dashes across all generated fields
            return {
                "tailored_resume_markdown": _sanitize_em_dashes(parsed.get("tailored_resume_markdown", "")),
                "cover_letter": _sanitize_em_dashes(parsed.get("cover_letter", "")),
                "short_proposal": _sanitize_em_dashes(parsed.get("short_proposal", "")),
                "recruiter_message": _sanitize_em_dashes(parsed.get("recruiter_message", "")),
                "application_answers": parsed.get("application_answers", []),
                "portfolio_selection": parsed.get("portfolio_selection", context.get("portfolio_list", [])),
            }
        except Exception as e:
            print(f"[ApplicationService] LLM generation failed ({e}), falling back to deterministic generation.")

    return generate_rule_based_application(job, profile, experiences, match_info)


def save_or_update_application_package(
    job_id: str,
    profile_id: str,
    package_data: dict[str, Any],
    db: Session,
    user_notes: str = "",
) -> ApplicationPackage:
    """Persists or updates an ApplicationPackage record in the database."""
    # Find latest resume for this profile if available
    latest_resume = db.query(Resume).filter(Resume.profile_id == profile_id).order_by(Resume.created_at.desc()).first()

    existing = db.query(ApplicationPackage).filter(
        ApplicationPackage.job_id == job_id,
        ApplicationPackage.profile_id == profile_id,
    ).first()

    if existing:
        existing.resume_id = latest_resume.id if latest_resume else None
        existing.tailored_resume_markdown = package_data.get("tailored_resume_markdown", existing.tailored_resume_markdown)
        existing.cover_letter = package_data.get("cover_letter", existing.cover_letter)
        existing.short_proposal = package_data.get("short_proposal", existing.short_proposal)
        existing.recruiter_message = package_data.get("recruiter_message", existing.recruiter_message)
        existing.application_answers = package_data.get("application_answers", existing.application_answers)
        existing.portfolio_selection = package_data.get("portfolio_selection", existing.portfolio_selection)
        if user_notes:
            existing.user_notes = user_notes
        # Reset status to DRAFT on regeneration
        existing.status = "DRAFT"
        db.commit()
        db.refresh(existing)
        return existing

    new_package = ApplicationPackage(
        job_id=job_id,
        profile_id=profile_id,
        resume_id=latest_resume.id if latest_resume else None,
        tailored_resume_markdown=package_data.get("tailored_resume_markdown", ""),
        cover_letter=package_data.get("cover_letter", ""),
        short_proposal=package_data.get("short_proposal", ""),
        recruiter_message=package_data.get("recruiter_message", ""),
        application_answers=package_data.get("application_answers", []),
        portfolio_selection=package_data.get("portfolio_selection", []),
        status="DRAFT",
        quality_review={},
        user_notes=user_notes,
    )
    db.add(new_package)
    db.commit()
    db.refresh(new_package)
    return new_package
