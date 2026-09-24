import os
import re
import json
from typing import BinaryIO
import anthropic

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    import docx
except ImportError:
    docx = None


def extract_raw_text_from_file(file_obj: BinaryIO, filename: str) -> str:
    """
    Extracts raw text from PDF, DOCX, TXT, or Markdown documents.
    Preserves original structure and never alters content.
    """
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".pdf":
        if not PdfReader:
            raise RuntimeError("pypdf is required to read PDF files.")
        reader = PdfReader(file_obj)
        pages_text = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages_text.append(text)
        return "\n\n".join(pages_text).strip()

    elif ext == ".docx":
        if not docx:
            raise RuntimeError("python-docx is required to read DOCX files.")
        doc = docx.Document(file_obj)
        paragraphs = [p.text for p in doc.paragraphs if p.text]
        return "\n".join(paragraphs).strip()

    elif ext in [".txt", ".md", ".rtf", ""]:
        content = file_obj.read()
        if isinstance(content, bytes):
            try:
                return content.decode("utf-8").strip()
            except UnicodeDecodeError:
                return content.decode("latin-1", errors="replace").strip()
        return str(content).strip()

    else:
        # Fallback: attempt utf-8 text read
        content = file_obj.read()
        if isinstance(content, bytes):
            try:
                return content.decode("utf-8").strip()
            except UnicodeDecodeError:
                return content.decode("latin-1", errors="replace").strip()
        return str(content).strip()


EXTRACT_RESUME_PROMPT = """You are a rigorous candidate resume parser.
Your duty is to extract structured career data with absolute truthfulness and 100% adherence to source text.

MANDATORY RULES:
1. NEVER INVENT, EXTRAPOLATE, ASSUME, OR EMBELLISH INFORMATION.
2. If metrics, dates, tools, skills, or achievements are not explicitly mentioned in the text, DO NOT invent them. Leave the lists empty or omit them.
3. For each experience record, extract:
   - company: exact company name
   - role: job title
   - dates: time period as written (e.g. 'Jan 2021 - Sep 2023')
   - start_date / end_date: approximate or exact dates if stated
   - responsibilities: explicit duties listed
   - achievements: accomplishments explicitly stated
   - metrics: numbers, percentages, or measurable results specifically cited in the text (e.g. 'Increased traffic by 40%')
   - tools: software, platforms, libraries explicitly mentioned (e.g. 'Google Analytics', 'Ahrefs', 'HubSpot')
   - skills: functional skills explicitly stated (e.g. 'SEO Audits', 'Content Strategy')
   - evidence: any cited links, portfolios, client names, publications or evidence mentioned in connection with this role
4. Return ONLY valid JSON matching this schema, without markdown formatting or preamble:
{{
  "summary": "...",
  "target_roles": ["..."],
  "skills": ["..."],
  "tools": ["..."],
  "certifications": ["..."],
  "achievements": ["..."],
  "portfolio_links": [{{"label": "...", "url": "..."}}],
  "education": [{{"institution": "...", "degree": "...", "year": "...", "details": "..."}}],
  "experiences": [
    {{
      "company": "...",
      "role": "...",
      "dates": "...",
      "start_date": "...",
      "end_date": "...",
      "responsibilities": ["..."],
      "achievements": ["..."],
      "metrics": ["..."],
      "tools": ["..."],
      "skills": ["..."],
      "evidence": [{{"title": "...", "url": "...", "quote": "...", "artifact_type": "link"}}]
    }}
  ]
}}

Source Resume Text:
{resume_text}
"""


def extract_structured_resume_data(resume_text: str) -> dict:
    """
    Parses resume text into structured fields using Anthropic Claude if available,
    falling back to a rule-based parser if unavailable.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key and not api_key.startswith("your-"):
        try:
            client = anthropic.Anthropic(api_key=api_key)
            prompt = EXTRACT_RESUME_PROMPT.format(resume_text=resume_text[:15000])
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
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
                return _normalize_parsed_data(parsed)
        except Exception as e:
            print(f"[RESUME_PARSER] Claude extraction failed: {e}. Falling back to rule-based parser.")

    return _rule_based_fallback_parser(resume_text)


def _normalize_parsed_data(data: dict) -> dict:
    """Ensures all expected keys and types exist."""
    return {
        "summary": str(data.get("summary") or ""),
        "target_roles": list(data.get("target_roles") or []),
        "skills": list(data.get("skills") or []),
        "tools": list(data.get("tools") or []),
        "certifications": list(data.get("certifications") or []),
        "achievements": list(data.get("achievements") or []),
        "portfolio_links": list(data.get("portfolio_links") or []),
        "education": list(data.get("education") or []),
        "experiences": list(data.get("experiences") or []),
    }


def _rule_based_fallback_parser(text: str) -> dict:
    """
    Heuristic rule-based extractor when LLM is unavailable.
    Identifies URLs, email addresses, skills, and experience sections truthfully.
    """
    urls = re.findall(r'https?://[^\s<>"]+|www\.[^\s<>"]+', text)
    portfolio_links = [{"label": f"Link {i+1}", "url": u} for i, u in enumerate(urls[:10])]

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    skills = []
    tools = []
    experiences = []
    summary = ""

    # Simple section scanner
    current_section = None
    section_buffers = {}

    for line in lines:
        lower = line.lower()
        if any(h in lower for h in ["experience", "work history", "employment", "professional experience"]) and len(line) < 35:
            current_section = "experience"
            section_buffers[current_section] = []
        elif any(h in lower for h in ["skills", "technical skills", "competencies", "core competencies"]) and len(line) < 35:
            current_section = "skills"
            section_buffers[current_section] = []
        elif any(h in lower for h in ["education", "academic background"]) and len(line) < 35:
            current_section = "education"
            section_buffers[current_section] = []
        elif any(h in lower for h in ["certifications", "certificates", "licenses"]) and len(line) < 35:
            current_section = "certifications"
            section_buffers[current_section] = []
        elif any(h in lower for h in ["summary", "profile", "about me", "objective"]) and len(line) < 35:
            current_section = "summary"
            section_buffers[current_section] = []
        elif current_section:
            section_buffers[current_section].append(line)

    if "summary" in section_buffers:
        summary = " ".join(section_buffers["summary"][:5])

    if "skills" in section_buffers:
        raw_skills_text = " ".join(section_buffers["skills"])
        # Split on commas, bullets, pipes
        tokens = [s.strip(" •·-|*") for s in re.split(r'[,|•·\n]', raw_skills_text) if len(s.strip(" •·-|*")) > 2]
        skills = list(dict.fromkeys(tokens))[:25]

    return {
        "summary": summary,
        "target_roles": [],
        "skills": skills,
        "tools": tools,
        "certifications": section_buffers.get("certifications", []),
        "achievements": [],
        "portfolio_links": portfolio_links,
        "education": [{"institution": line, "degree": "", "year": "", "details": ""} for line in section_buffers.get("education", [])[:4]],
        "experiences": experiences,
    }
