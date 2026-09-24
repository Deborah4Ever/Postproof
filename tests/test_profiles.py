import os
import io
import sys
from fastapi.testclient import TestClient

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app.db.session import SessionLocal
from app.services.profile_service import seed_default_profiles


def run_tests():
    print("=== 1. Testing Database & Lifespan Seeding ===")
    with TestClient(app) as client:
        resp = client.get("/api/profiles")
        assert resp.status_code == 200, f"Failed to list profiles: {resp.text}"
        profiles = resp.json()
        print(f"Total seeded profiles: {len(profiles)}")
        profile_names = [p["name"] for p in profiles]
        print(f"Profiles: {profile_names}")

        required_profiles = [
            "SEO / Content / Digital Marketing",
            "Social Media Marketing / Social Media Management",
            "Customer Support / Customer Success / Customer Experience",
            "Web3 / Community / Operations",
        ]
        for rp in required_profiles:
            assert rp in profile_names, f"Missing required initial profile: {rp}"
        print("PASS: 4 initial career profiles verified.")

        print("\n=== 2. Testing Profile Creation & Editing ===")
        # Create a custom test profile
        new_prof_data = {
            "name": "AI Solutions Engineer",
            "target_roles": ["AI Engineer", "LLM Solutions Architect"],
            "summary": "Specialist in production agentic systems and LLM orchestration.",
            "skills": ["Python", "FastAPI", "Prompt Engineering", "Vector Databases"],
            "tools": ["LangChain", "OpenAI", "Anthropic", "PostgreSQL"],
            "certifications": ["AWS Certified Machine Learning"],
            "achievements": ["Built agent processing 10k items daily"],
            "portfolio_links": [{"label": "GitHub", "url": "https://github.com/example/ai-agent"}],
            "education": [{"institution": "Tech University", "degree": "B.S. Computer Science", "year": "2020"}],
        }
        create_resp = client.post("/api/profiles", json=new_prof_data)
        assert create_resp.status_code == 201, f"Failed to create profile: {create_resp.text}"
        created_profile = create_resp.json()
        created_id = created_profile["id"]
        print(f"Created profile ID: {created_id}")

        # Edit the profile
        edit_resp = client.put(f"/api/profiles/{created_id}", json={
            "summary": "Updated summary: Enterprise AI systems and automated workflows.",
            "skills": ["Python", "FastAPI", "Prompt Engineering", "Alembic", "Redis"],
        })
        assert edit_resp.status_code == 200
        assert "Alembic" in edit_resp.json()["skills"]
        print("PASS: Profile creation and editing verified.")

        print("\n=== 3. Testing Resume Upload & Extraction ===")
        sample_resume_text = """
JOHN DOE - CONTENT & SEO LEAD
Email: john@example.com | Portfolio: https://johndoe.com | GitHub: https://github.com/johndoe

SUMMARY
Results-driven Content Strategist with 5+ years driving organic growth for SaaS companies.

EXPERIENCE
Acme Corp | Lead Content Strategist
June 2021 - Present
- Formulated and executed organic content strategy for B2B cybersecurity products.
- Grew organic inbound traffic from 25,000 to 180,000 monthly unique visitors (+620%).
- Reduced CAC by 35% across all organic lead capture funnels.
- Tools: Ahrefs, Google Search Console, Google Analytics 4, Clearscope, WordPress.
- Skills: Technical SEO, Keyword Mapping, Conversion Rate Optimization.
- Evidence: Live public case study at https://johndoe.com/cases/acme-growth.

Beta Media | Digital Marketing Specialist
Jan 2019 - May 2021
- Produced 150+ in-depth technical guides and case studies.
- Managed editorial calendar and team of 4 freelance writers.
- Increased organic search rankings for 85 targeted primary keywords to top 3 positions.
- Tools: Semrush, HubSpot, Canva, Google Docs.
- Skills: Content Planning, On-Page Optimization, Freelancer Management.

EDUCATION
State University, B.A. in English & Communications, 2018

CERTIFICATIONS
- Google Analytics Individual Qualification (GA4)
- HubSpot Inbound Marketing Certified
"""
        file_bytes = sample_resume_text.encode("utf-8")
        seo_profile = next(p for p in profiles if "SEO" in p["name"])
        seo_profile_id = seo_profile["id"]

        upload_resp = client.post(
            "/api/resumes/upload",
            files={"file": ("john_doe_resume.txt", io.BytesIO(file_bytes), "text/plain")},
            data={"profile_id": seo_profile_id, "auto_extract": True},
        )
        assert upload_resp.status_code == 201, f"Upload failed: {upload_resp.text}"
        upload_data = upload_resp.json()
        resume_record = upload_data["resume"]
        resume_id = resume_record["id"]
        print(f"Uploaded Resume ID: {resume_id}, filename: {resume_record['filename']}")
        assert len(resume_record["raw_text"]) > 100, "Raw text was not extracted from uploaded resume"

        # Verify source document retrieval
        download_resp = client.get(f"/api/resumes/{resume_id}/download")
        assert download_resp.status_code == 200, "Failed to download original resume document"
        assert download_resp.content == file_bytes, "Original document bytes do not match source"
        print("PASS: Raw file storage and unmodified download verified.")

        print("\n=== 4. Testing Structured Experience and Evidence Layer ===")
        # Check experience records linked to the profile
        exp_resp = client.get(f"/api/experiences?profile_id={seo_profile_id}")
        assert exp_resp.status_code == 200
        experiences = exp_resp.json()
        print(f"Total structured experiences for profile: {len(experiences)}")

        # Create a detailed structured experience with full evidence layer
        custom_exp_payload = {
            "profile_id": seo_profile_id,
            "resume_id": resume_id,
            "company": "Gamma Technologies",
            "role": "Senior Growth Marketer",
            "dates": "Mar 2023 - Present",
            "start_date": "2023-03-01",
            "end_date": None,
            "responsibilities": [
                "Led end-to-end SEO strategy and technical site overhaul across 4 domains",
                "Managed $50k monthly content production budget and editorial QA",
            ],
            "achievements": [
                "Scaled organic pipeline to $1.2M ARR in 14 months",
                "Ranked #1 for 30 high-intent enterprise SaaS search keywords",
            ],
            "metrics": [
                "+140% organic demo signups YoY",
                "$1.2M ARR attributed to organic content",
                "0.4s average Core Web Vitals LCP improvement",
            ],
            "tools": [
                "Ahrefs",
                "Google Search Console",
                "Next.js",
                "Vercel Analytics",
                "Clearscope",
            ],
            "skills": [
                "Enterprise SEO",
                "Technical Site Architecture",
                "Content Funnel Optimization",
                "Data Analysis",
            ],
            "evidence": [
                {
                    "title": "Ahrefs Organic Traffic Growth Graph",
                    "url": "https://gamma.io/press/growth-milestone",
                    "quote": "Gamma achieves record organic inbound quarter led by John.",
                    "artifact_type": "metric_report",
                },
                {
                    "title": "Published Next.js SEO Architecture Playbook",
                    "url": "https://gamma.io/blog/nextjs-seo-playbook",
                    "artifact_type": "link",
                },
            ],
        }
        create_exp_resp = client.post("/api/experiences", json=custom_exp_payload)
        assert create_exp_resp.status_code == 201, f"Failed to create experience: {create_exp_resp.text}"
        exp_record = create_exp_resp.json()
        exp_id = exp_record["id"]
        print(f"Created Experience ID: {exp_id} for company: {exp_record['company']}")
        assert len(exp_record["evidence"]) == 2
        assert len(exp_record["metrics"]) == 3

        # Edit experience record
        update_exp_resp = client.put(f"/api/experiences/{exp_id}", json={
            "metrics": [
                "+165% organic demo signups YoY (audited)",
                "$1.4M ARR attributed to organic content",
            ]
        })
        assert update_exp_resp.status_code == 200
        assert "+165%" in update_exp_resp.json()["metrics"][0]
        print("PASS: Full structured experience and evidence layer verified.")

        print("\n=== 5. Testing Resume Replacement ===")
        updated_resume_text = sample_resume_text + "\nADDITIONAL NOTE: Promoted to VP of Organic Growth."
        replace_resp = client.post(
            "/api/resumes/upload",
            files={"file": ("john_doe_v2.txt", io.BytesIO(updated_resume_text.encode("utf-8")), "text/plain")},
            data={"profile_id": seo_profile_id, "replace_resume_id": resume_id, "auto_extract": False},
        )
        assert replace_resp.status_code == 201
        replaced_data = replace_resp.json()["resume"]
        assert replaced_data["id"] == resume_id
        assert replaced_data["filename"] == "john_doe_v2.txt"
        assert "VP of Organic Growth" in replaced_data["raw_text"]
        print("PASS: Resume replacement without losing ID link verified.")

        print("\n=== 6. Testing Profile Deletion & Cleanup ===")
        del_resp = client.delete(f"/api/profiles/{created_id}")
        assert del_resp.status_code == 204
        get_deleted = client.get(f"/api/profiles/{created_id}")
        assert get_deleted.status_code == 404
        print("PASS: Profile deletion verified.")

    print("\nALL CANDIDATE PROFILE & RESUME TESTS PASSED!")


if __name__ == "__main__":
    run_tests()
