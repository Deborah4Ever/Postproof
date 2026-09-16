"""
This is the one step in the whole pipeline that has to actually reason,
not just fetch. Everything upstream is tool calls (like Financial
Datasets serving up a number); this is the agent.
"""

import json
import os
import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

EXTRACT_FIELDS_PROMPT = """You are extracting structured job posting fields from raw page \
content fetched from {url}. The page may include site navigation, "similar jobs" sections, \
cookie banners, and other noise alongside the actual posting - ignore anything that isn't part \
of THIS specific job posting.

Page content:
{content}

Respond with ONLY valid JSON, no other text:
{{
  "found_posting": <true if this page contains an actual single job posting, false if it's a \
blocked/error page, a listing/search page, or otherwise has no posting to extract>,
  "title": <string, the job title, or null>,
  "company": <string, the hiring company's name, or null>,
  "description": <string, the job description/responsibilities/requirements text for this \
posting, trimmed to at most 1500 characters of the essential content (drop boilerplate EEO/\
benefits/legal text), or null>,
  "posted_at": <ISO 8601 date string if a specific post date is stated on the page, else null - \
do not guess>
}}
"""


def extract_posting_fields(page_content: str, url: str) -> dict | None:
    """
    Asks Claude to pull {title, company, description, posted_at} out of raw
    scraped page content. Job posting pages vary too much in structure across
    sites for reliable regex/CSS-selector scraping, so the model reads it
    instead. Returns None if the page had no extractable posting or the
    model's response couldn't be parsed - never raises, so one bad page
    can't crash the request; the caller turns None into an honest failure.
    """
    prompt = EXTRACT_FIELDS_PROMPT.format(url=url, content=page_content[:8000])

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not parsed.get("found_posting") or not parsed.get("title"):
        return None

    return parsed


VERDICT_PROMPT = """You are checking whether a job posting is real or a "ghost job" \
(posted but never intended to be filled - common for data harvesting, pipeline-building, \
or fake openings that make a company look like it's growing).

Posting:
Title: {title}
Company: {company}
Description: {description}

Signals gathered:
- Freshness: {freshness}
- A named hiring manager was found for this role at this company: {hiring_manager_found}
- Company web/social activity evidence: {legitimacy_evidence}
- Verified contact enrichment succeeded: {contact_found}

Weigh these signals against each other - do not just count them. Freshness is a gradient, not \
a single bucket: a posting from a few hours or 1-2 days ago is a meaningfully stronger positive \
signal than one from 10-13 days ago, even though a flat cutoff would call both "recent" - do \
not collapse that difference into one bucket. A company with strong general web presence but \
zero specific evidence tied to THIS role, posted a long time ago, with no named hiring manager, \
is a stronger "ghost" signal than a very recently posted listing missing only one weak signal. \
An unknown posting date is a neutral-to-cautionary signal, not a positive one - do not treat it \
as equivalent to a fresh posting.

Respond with ONLY valid JSON, no other text:
{{
  "verdict": "real" | "suspicious" | "ghost",
  "confidence": <float 0-1>,
  "evidence": [<2-4 short strings citing the specific signals that drove this>],
  "pitch_line": <one sentence a writer/recruiter could use to reference this specific \
posting in an outreach message, or null if verdict is "ghost">
}}
"""


FAILURE_MESSAGES = {
    "company legitimacy check": "company legitimacy check unavailable - source returned an error",
    "hiring manager check": "hiring manager check unavailable - source returned an error",
    "contact enrichment": "contact enrichment unavailable - source returned an error",
}


def synthesize_verdict(
    posting: dict,
    freshness: str,
    legitimacy_evidence,
    hiring_manager_found: bool,
    contact: dict | None,
    failed_signals: list[str] | None = None,
) -> dict:
    failed_signals = failed_signals or []
    prompt = VERDICT_PROMPT.format(
        title=posting.get("title", ""),
        company=posting.get("company", ""),
        description=posting.get("description", "")[:1000],
        freshness=freshness,
        hiring_manager_found=hiring_manager_found,
        legitimacy_evidence=json.dumps(legitimacy_evidence)[:1500],
        contact_found=contact is not None,
    )
    if failed_signals:
        # Tell the model which signals are missing due to a source error, not
        # a real absence of evidence - don't let it read "unknown" as "ghost".
        prompt += (
            "\n\nNote: the following signals could not be checked because the "
            "underlying source errored out (treat as unknown, not as a negative "
            "signal): " + ", ".join(failed_signals)
        )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    # claude-sonnet-4-6 wraps JSON in ```json...``` even when told not to — strip the fence.
    if text.startswith("```"):
        text = text.split("```", 2)[1]          # drop opening ```[json]
        if text.startswith("json"):
            text = text[4:]                      # drop the "json" language tag
        text = text.rsplit("```", 1)[0].strip()  # drop closing ```
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # fallback so one bad model response doesn't crash a batch run -
        # this IS your "handles bad inputs" requirement, keep it visible
        # in the demo rather than hiding failures
        parsed = {
            "verdict": "suspicious",
            "confidence": 0.0,
            "evidence": ["verdict synthesis failed - flagged for manual review"],
            "pitch_line": None,
        }

    # Don't rely on the model to mention this unprompted - guarantee every
    # failed signal shows up plainly in the evidence array so a partial
    # result never reads as "fully checked" when it wasn't.
    evidence = list(parsed["evidence"])
    for signal in failed_signals:
        evidence.append(FAILURE_MESSAGES.get(signal, f"{signal} unavailable - source returned an error"))
    if freshness == "posting date unknown":
        evidence.append("posting date could not be determined")

    return {
        "title": posting.get("title"),
        "company": posting.get("company"),
        "verdict": parsed["verdict"],
        "confidence": parsed["confidence"],
        "evidence": evidence,
        "pitch_line": parsed["pitch_line"],
        "contact": contact,
    }
