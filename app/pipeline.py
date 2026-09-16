"""
The 5-step chain. Each step is a real Monid call except the freshness
filter, which is pure logic - don't spend money checking staleness,
that's free to compute from the scraped date.
"""

from datetime import datetime, timedelta

from .monid_client import (
    call_monid_tool,
    TOOL_JOB_SCRAPE,
    TOOL_COMPANY_SEARCH,
    TOOL_PEOPLE_SEARCH,
    TOOL_ENRICHMENT,
    TOOL_URL_EXTRACT,
)
from .verdict import synthesize_verdict, extract_posting_fields


def extract_posting_from_url(url: str) -> dict:
    """
    Fetches a job posting page's content via Monid and asks Claude to pull
    the structured fields out of it - pages vary too much in structure
    across LinkedIn/Indeed/careers-site layouts for regex/CSS-selector
    scraping to be reliable.

    Returns the same shape check_one_posting() expects as its posting
    argument on success: {title, company, description, posted_at, url}.
    On any failure (fetch error, blocked page, no posting content, model
    couldn't extract fields), returns {"failed": True, "error": ..., ...}
    instead of raising - same honest-failure pattern as the rest of this file.
    """
    result = call_monid_tool(TOOL_URL_EXTRACT, {"url": url})
    if not result.success:
        return {"failed": True, "error": "could not fetch the page", "cost_usd": result.cost_usd}

    data = result.data or {}
    markdown = data.get("markdown")
    if not data.get("success") or not markdown:
        return {
            "failed": True,
            "error": "page returned no usable content (blocked or empty)",
            "cost_usd": result.cost_usd,
        }

    fields = extract_posting_fields(markdown, url)
    if fields is None:
        return {
            "failed": True,
            "error": "no job posting found in the page content",
            "cost_usd": result.cost_usd,
        }

    return {
        "title": fields.get("title") or "",
        "company": fields.get("company") or "",
        "description": fields.get("description") or "",
        "posted_at": fields.get("posted_at"),
        "url": url,
    }


def scrape_postings(role: str, location: str, limit: int = 20) -> list[dict]:
    # Map common search terms to Wellfound's role slug taxonomy
    role_slug = role.lower().strip().replace(" ", "-")
    slug_map = {
        "content-writer": "copywriter",
        "writer": "copywriter",
        "content": "copywriter",
        "content-marketing": "marketing",
    }
    role_slug = slug_map.get(role_slug, role_slug)
    loc_slug = location.lower().strip().replace(" ", "-")

    result = call_monid_tool(
        TOOL_JOB_SCRAPE,
        {"role": role_slug, "location": loc_slug},
    )
    data = result.data
    raw_jobs = []
    if isinstance(data, list):
        raw_jobs = data
    elif isinstance(data, dict):
        # Wellfound returns output: {"status": 200, "data": {"jobs": [...]}}
        if "data" in data and isinstance(data["data"], dict):
            raw_jobs = data["data"].get("jobs", [])
        else:
            raw_jobs = data.get("jobs", data.get("postings", []))

    postings = []
    for job in (raw_jobs or [])[:limit]:
        if not isinstance(job, dict):
            continue
        startup = job.get("startup") if isinstance(job.get("startup"), dict) else {}
        company = (
            job.get("company")
            or job.get("company_name")
            or startup.get("name")
            or "Unknown Company"
        )
        postings.append({
            "title": job.get("title", role),
            "company": company,
            "description": job.get("description") or job.get("snippet") or f"{job.get('title', role)} at {company}",
            "posted_at": job.get("posted_at") or job.get("created_at"),
            "url": job.get("url") or (f"https://wellfound.com/jobs/{job.get('id')}" if job.get("id") else None),
        })
    return postings


def is_fresh(posting: dict, max_age_days: int = 14) -> bool:
    posted_at = posting.get("posted_at")
    if not posted_at:
        return True  # unknown age - let it through, flag it downstream instead of dropping it
    try:
        posted_dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return (datetime.now(posted_dt.tzinfo) - posted_dt) <= timedelta(days=max_age_days)


def check_company_legitimacy(company_name: str) -> dict:
    result = call_monid_tool(
        TOOL_COMPANY_SEARCH,
        # surf /search/web query param is 'q', not 'query'
        {"q": f"{company_name} company news careers hiring"},
    )
    if not result.success:
        return {"evidence": None, "failed": True, "cost_usd": result.cost_usd}
    # surf returns output.data as a list of search result objects
    return {"evidence": result.data, "failed": False, "cost_usd": result.cost_usd}


def check_hiring_manager_exists(company_name: str, role_title: str) -> dict:
    result = call_monid_tool(
        TOOL_PEOPLE_SEARCH,
        # apollo /mixed_people/api_search: 'q_keywords' is the free-text filter;
        # person_titles[] would be ideal but q_keywords works as a broad match.
        {"q_keywords": f"{role_title} {company_name}"},
    )
    if not result.success:
        return {"found": False, "raw": None, "failed": True, "cost_usd": result.cost_usd}
    # apollo returns people list under output directly (result.data IS the output obj)
    people = result.data.get("people", [])
    return {"found": bool(people), "raw": result.data, "failed": False, "cost_usd": result.cost_usd}


def enrich_contact(person_name: str, company_name: str) -> dict | None:
    """
    Only call this AFTER a posting has passed the legitimacy check.
    This is the expensive step (~$0.05/call) - don't spend it on
    postings you're about to flag as fake.
    apollo /people/match returns 400 if name is empty ('Too small') -
    guard here so a missing name field never crashes the batch.
    Returns None only when enrichment was never attempted (empty name);
    returns {"failed": True, ...} when it was attempted but Monid errored,
    so callers can tell "skipped" apart from "tried and failed".
    """
    if not person_name or not person_name.strip():
        return None
    result = call_monid_tool(
        TOOL_ENRICHMENT,
        # apollo /people/match: 'organization_name' not 'company'
        {"name": person_name, "organization_name": company_name},
    )
    if not result.success:
        return {"data": None, "failed": True, "cost_usd": result.cost_usd}
    return {"data": result.data, "failed": False, "cost_usd": result.cost_usd}


def check_one_posting(posting: dict) -> dict:
    """
    Runs the full chain for a single posting and returns a verdict record.
    This is the function your API route and your batch script both call.
    """
    company = posting.get("company", "")
    title = posting.get("title", "")

    fresh = is_fresh(posting)
    legitimacy = check_company_legitimacy(company)
    hiring_manager = check_hiring_manager_exists(company, title)

    contact = None
    contact_result = None
    # SHORT-CIRCUIT: only pay for enrichment if this looks like a real,
    # fresh posting with a real hiring manager - this is the single
    # biggest cost lever in the whole pipeline (enrichment is ~half
    # the per-check cost).
    looks_legit_enough = fresh and hiring_manager["found"]
    if looks_legit_enough and (hiring_manager.get("raw") or {}).get("people"):
        first_person = hiring_manager["raw"]["people"][0]
        # Apollo people search returns first_name (last_name is obfuscated by design).
        # Use first_name + last_name_obfuscated as the best available identifier.
        person_name = " ".join(filter(None, [
            first_person.get("first_name", ""),
            first_person.get("last_name_obfuscated", ""),
        ])).strip()
        contact_result = enrich_contact(person_name, company)
        if contact_result is not None and not contact_result["failed"]:
            contact = contact_result["data"]

    # Surface which steps errored out so the verdict stays honest about what
    # it couldn't check, instead of quietly treating "unknown" as "negative".
    failed_signals = []
    if legitimacy.get("failed"):
        failed_signals.append("company legitimacy check")
    if hiring_manager.get("failed"):
        failed_signals.append("hiring manager check")
    if contact_result is not None and contact_result.get("failed"):
        failed_signals.append("contact enrichment")

    verdict = synthesize_verdict(
        posting=posting,
        fresh=fresh,
        legitimacy_evidence=legitimacy["evidence"],
        hiring_manager_found=hiring_manager["found"],
        contact=contact,
        failed_signals=failed_signals,
    )
    return verdict
