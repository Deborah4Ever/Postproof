"""
Job Discovery and External Research Service.

Connects to legitimate public job APIs, feeds, and permitted sources:
- We Work Remotely (official public RSS feeds)
- Jobicy (official public Remote Jobs API)
- Arbeitnow (official public API)
- Public ATS APIs (Ashby public job boards, SmartRecruiters public postings)
- Monid API (Wellfound, Y Combinator, etc.)

Strictly follows ethical integration guidelines:
- No unauthorized scraping
- No bypassing CAPTCHAs
- No bypassing authentication
- No circumventing platform restrictions
- No fake accounts
"""

import re
import hashlib
import urllib.parse
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
import httpx
from sqlalchemy.orm import Session

from ..db.models import Job
from .monid_service import monid_service

# Target Category Mappings
TARGET_CATEGORIES = [
    "SEO",
    "SEO Content",
    "Digital Marketing",
    "Social Media Marketing",
    "Social Media Management",
    "Customer Support",
    "Customer Success",
    "Customer Experience",
    "Community Management",
    "Web3",
    "Crypto",
    "Community Operations",
    "Operations",
]

TARGET_REGIONS = [
    "USA", "United States", "US",
    "UK", "United Kingdom", "Great Britain",
    "Europe", "EU", "Germany", "France", "Netherlands", "Spain", "Ireland",
    "Australia", "AU",
    "New Zealand", "NZ",
    "Worldwide", "Anywhere", "Global", "Remote",
]


def normalize_url(url: str) -> str:
    """Strips tracking query parameters (utm_*, ref, etc.) and trailing slashes."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
        clean_qs = urllib.parse.parse_qsl(parsed.query)
        filtered_qs = [(k, v) for k, v in clean_qs if not k.lower().startswith(("utm_", "ref", "source", "fbclid"))]
        new_query = urllib.parse.urlencode(filtered_qs)
        clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", new_query, ""))
        return clean
    except Exception:
        return url.strip()


def generate_dedup_key(source: str, source_job_id: str | None, company: str, title: str, url: str) -> str:
    """
    Implements multi-level duplicate detection:
    1. If source_job_id exists: hash(source + ":" + source_job_id)
    2. Otherwise: hash(normalized company + ":" + normalized title + ":" + normalized url)
    """
    if source_job_id and source_job_id.strip():
        raw = f"{source.lower().strip()}:{source_job_id.strip()}"
    else:
        norm_company = re.sub(r'[^a-z0-9]', '', company.lower())
        norm_title = re.sub(r'[^a-z0-9]', '', title.lower())
        norm_url = normalize_url(url).lower()
        raw = f"{norm_company}:{norm_title}:{norm_url}"

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def detect_category(title: str, description: str = "") -> str:
    """Maps job title and text to the most relevant target career category."""
    text = f"{title} {description}".lower()

    if any(k in text for k in ["web3", "crypto", "blockchain", "solidity", "dao", "tokenomics", "defi"]):
        if any(k in text for k in ["community", "discord", "moderator", "social"]):
            return "Community Management"
        return "Web3 / Crypto"

    if any(k in text for k in ["seo", "search engine optimization", "organic search", "link building"]):
        if "content" in text or "writer" in text:
            return "SEO Content"
        return "SEO"

    if any(k in text for k in ["social media", "instagram", "tiktok", "twitter", "linkedin management"]):
        if "manager" in text or "management" in text:
            return "Social Media Management"
        return "Social Media Marketing"

    if any(k in text for k in ["content marketing", "content strategist", "digital marketing", "growth marketer"]):
        return "Digital Marketing"

    if any(k in text for k in ["customer success", "csm", "client success"]):
        return "Customer Success"

    if any(k in text for k in ["customer support", "support specialist", "technical support", "ticket triage"]):
        return "Customer Support"

    if any(k in text for k in ["customer experience", "cx specialist", "client experience"]):
        return "Customer Experience"

    if any(k in text for k in ["community manager", "community lead", "community specialist"]):
        return "Community Management"

    if any(k in text for k in ["operations manager", "community operations", "support operations", "ops"]):
        return "Operations"

    return "Digital Marketing"


def matches_target_region(location: str, country: str = "") -> bool:
    """Checks whether the job permits candidates from target regions or is globally remote."""
    loc_text = f"{location} {country}".lower()
    if not loc_text.strip():
        return True  # Unspecified remote defaults to allowed for review

    allowed_terms = [
        "worldwide", "anywhere", "remote", "global", "us", "usa", "united states",
        "uk", "united kingdom", "europe", "emea", "eu", "australia", "new zealand",
        "canada", "germany", "france", "ireland", "spain", "netherlands", "london", "sydney"
    ]
    return any(term in loc_text for term in allowed_terms)


# ----------------------------------------------------------------------
# Public Feed Fetchers
# ----------------------------------------------------------------------
def fetch_jobicy_jobs(limit: int = 50) -> list[dict]:
    """Fetches legitimate jobs from Jobicy's official public API."""
    url = f"https://jobicy.com/api/v2/remote-jobs?count={limit}"
    results = []
    try:
        with httpx.Client(timeout=15.0, headers={"User-Agent": "JobAgent/1.0"}) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                jobs = resp.json().get("jobs", [])
                for j in jobs:
                    industries = j.get("jobIndustry") or []
                    if isinstance(industries, str):
                        reqs = [s.strip() for s in industries.split(",") if s.strip()]
                    elif isinstance(industries, list):
                        reqs = [str(s).strip() for s in industries if s]
                    else:
                        reqs = []
                    results.append({
                        "source": "jobicy",
                        "source_job_id": str(j.get("id") or ""),
                        "job_url": j.get("url") or "",
                        "company": j.get("companyName") or "Unknown",
                        "job_title": j.get("jobTitle") or "",
                        "description": j.get("jobDescription") or j.get("jobExcerpt") or "",
                        "location": j.get("jobGeo") or "Remote",
                        "country": j.get("jobGeo") or "",
                        "remote_status": "Remote",
                        "employment_type": j.get("jobType") or "Full-time",
                        "salary": j.get("annualSalaryMin") and f"${j.get('annualSalaryMin')} - ${j.get('annualSalaryMax', '')}" or None,
                        "posted_at": _parse_date(j.get("pubDate")),
                        "requirements": reqs,
                    })
    except Exception as e:
        print(f"[DISCOVERY] Jobicy error: {e}")
    return results


def fetch_arbeitnow_jobs() -> list[dict]:
    """Fetches legitimate jobs from Arbeitnow's public job board API."""
    url = "https://www.arbeitnow.com/api/job-board-api"
    results = []
    try:
        with httpx.Client(timeout=15.0, headers={"User-Agent": "JobAgent/1.0"}) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                jobs = resp.json().get("data", [])
                for j in jobs:
                    results.append({
                        "source": "arbeitnow",
                        "source_job_id": str(j.get("slug") or ""),
                        "job_url": j.get("url") or "",
                        "company": j.get("company_name") or "Unknown",
                        "job_title": j.get("title") or "",
                        "description": j.get("description") or "",
                        "location": j.get("location") or "Remote",
                        "country": j.get("location") or "",
                        "remote_status": "Remote" if j.get("remote") else "Hybrid/Onsite",
                        "employment_type": (j.get("job_types") or ["Full-time"])[0] if j.get("job_types") else "Full-time",
                        "salary": None,
                        "posted_at": datetime.fromtimestamp(j.get("created_at", 0), tz=timezone.utc) if j.get("created_at") else None,
                        "requirements": j.get("tags") or [],
                    })
    except Exception as e:
        print(f"[DISCOVERY] Arbeitnow error: {e}")
    return results


def fetch_weworkremotely_jobs() -> list[dict]:
    """Fetches jobs from We Work Remotely official public RSS feed."""
    url = "https://weworkremotely.com/remote-jobs.rss"
    results = []
    try:
        with httpx.Client(timeout=15.0, headers={"User-Agent": "JobAgent/1.0"}) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                for item in root.findall(".//item"):
                    title_elem = item.find("title")
                    link_elem = item.find("link")
                    desc_elem = item.find("description")
                    pub_elem = item.find("pubDate")

                    full_title = title_elem.text if title_elem is not None else ""
                    link = link_elem.text if link_elem is not None else ""
                    desc = desc_elem.text if desc_elem is not None else ""

                    # Split Title format: "Company: Job Title"
                    company = "Unknown"
                    job_title = full_title
                    if ":" in full_title:
                        parts = full_title.split(":", 1)
                        company = parts[0].strip()
                        job_title = parts[1].strip()

                    results.append({
                        "source": "weworkremotely",
                        "source_job_id": link.split("/")[-1] if link else "",
                        "job_url": link,
                        "company": company,
                        "job_title": job_title,
                        "description": desc,
                        "location": "Worldwide Remote",
                        "country": "Worldwide",
                        "remote_status": "Remote",
                        "employment_type": "Full-time",
                        "salary": None,
                        "posted_at": _parse_date(pub_elem.text if pub_elem is not None else None),
                        "requirements": [],
                    })
    except Exception as e:
        print(f"[DISCOVERY] WWR error: {e}")
    return results


def fetch_monid_jobs(role: str = "content writer", location: str = "remote") -> list[dict]:
    """Fetches startup jobs via Monid's confirmed Wellfound or YC endpoints."""
    if not monid_service.is_configured():
        return []

    # Map role to Wellfound taxonomy
    role_slug = role.lower().strip().replace(" ", "-")
    res = monid_service.execute_endpoint(
        provider="wellfound",
        endpoint="/search_jobs",
        query_params={"role": role_slug, "location": location},
    )
    if not res.success or not res.data:
        return []

    data = res.data
    raw_jobs = []
    if isinstance(data, list):
        raw_jobs = data
    elif isinstance(data, dict):
        if "data" in data and isinstance(data["data"], dict):
            raw_jobs = data["data"].get("jobs", [])
        else:
            raw_jobs = data.get("jobs", [])

    results = []
    for j in raw_jobs:
        if not isinstance(j, dict):
            continue
        startup = j.get("startup") if isinstance(j.get("startup"), dict) else {}
        company = j.get("company") or startup.get("name") or "Unknown"
        title = j.get("title") or role
        job_id = str(j.get("id") or "")
        url = j.get("url") or (f"https://wellfound.com/jobs/{job_id}" if job_id else "")

        results.append({
            "source": "monid:wellfound",
            "source_job_id": job_id,
            "job_url": url,
            "company": company,
            "job_title": title,
            "description": j.get("description") or j.get("snippet") or f"{title} at {company}",
            "location": j.get("location") or "Remote",
            "country": "Worldwide",
            "remote_status": "Remote",
            "employment_type": "Full-time",
            "salary": None,
            "posted_at": datetime.now(timezone.utc),
            "requirements": [],
        })
    return results


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_str)
    except Exception:
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            return None


# ----------------------------------------------------------------------
# Core Discovery Pipeline (with Deduplication & Verification Status)
# ----------------------------------------------------------------------
def run_discovery_pipeline(
    db: Session,
    sources: list[str] | None = None,
    category_filter: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Executes multi-source job discovery, performs deduplication,
    classifies target categories, checks target regions, and records new jobs in DB.
    """
    sources_to_run = sources or ["jobicy", "weworkremotely", "arbeitnow", "monid"]
    collected_raw_jobs: list[dict] = []

    if "jobicy" in sources_to_run:
        collected_raw_jobs.extend(fetch_jobicy_jobs(limit=50))
    if "weworkremotely" in sources_to_run:
        collected_raw_jobs.extend(fetch_weworkremotely_jobs())
    if "arbeitnow" in sources_to_run:
        collected_raw_jobs.extend(fetch_arbeitnow_jobs())
    if "monid" in sources_to_run and monid_service.is_configured():
        collected_raw_jobs.extend(fetch_monid_jobs(role="copywriter"))

    new_jobs_count = 0
    duplicate_count = 0
    ineligible_region_count = 0

    for raw in collected_raw_jobs:
        company = raw.get("company", "Unknown").strip()
        title = raw.get("job_title", "").strip()
        url = raw.get("job_url", "").strip()

        if not title or not url or not company:
            continue

        # Region compatibility check
        loc = raw.get("location", "")
        country = raw.get("country", "")
        if not matches_target_region(loc, country):
            ineligible_region_count += 1
            continue

        detected_cat = detect_category(title, raw.get("description", ""))
        if category_filter and category_filter.lower() not in detected_cat.lower():
            continue

        norm_url = normalize_url(url)
        dedup_key = generate_dedup_key(
            source=raw["source"],
            source_job_id=raw.get("source_job_id"),
            company=company,
            title=title,
            url=url,
        )

        # Check existing by dedup_key or normalized_url
        existing = db.query(Job).filter(
            (Job.dedup_key == dedup_key) | (Job.normalized_url == norm_url)
        ).first()

        if existing:
            duplicate_count += 1
            continue

        emp_type = raw.get("employment_type")
        if isinstance(emp_type, list):
            emp_type = ", ".join(str(x) for x in emp_type if x) or "Full-time"
        elif not emp_type:
            emp_type = "Full-time"
        else:
            emp_type = str(emp_type)

        loc_val = loc if isinstance(loc, str) else (", ".join(str(x) for x in loc) if isinstance(loc, list) else "Remote")
        country_val = country if isinstance(country, str) else (", ".join(str(x) for x in country) if isinstance(country, list) else "")
        salary_val = str(raw.get("salary")) if raw.get("salary") is not None else None

        new_job = Job(
            source=str(raw["source"]),
            source_job_id=str(raw.get("source_job_id")) if raw.get("source_job_id") else None,
            job_url=url,
            normalized_url=norm_url,
            company=company,
            job_title=title,
            description=raw.get("description", ""),
            location=loc_val or "Remote",
            country=country_val,
            remote_status=str(raw.get("remote_status") or "Remote"),
            employment_type=emp_type,
            salary=salary_val,
            posted_at=raw.get("posted_at") or datetime.now(timezone.utc),
            requirements=raw.get("requirements", []),
            preferred_requirements=[],
            skills=[s for s in raw.get("requirements", []) if len(s) < 30],
            detected_category=detected_cat,
            status="UNVERIFIED",  # Starts unverified, awaiting verification checks
            verification_details={},
            dedup_key=dedup_key,
        )
        db.add(new_job)
        new_jobs_count += 1

    db.commit()

    return {
        "sources_scanned": sources_to_run,
        "total_fetched": len(collected_raw_jobs),
        "new_jobs_added": new_jobs_count,
        "duplicates_skipped": duplicate_count,
        "ineligible_region_skipped": ineligible_region_count,
    }
