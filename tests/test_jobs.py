import os
import sys
from fastapi.testclient import TestClient

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app.services.monid_service import monid_service


def run_job_layer_tests():
    print("=== 1. Testing Monid Service Integration ===")
    print(f"Monid configured: {monid_service.is_configured()}")
    endpoints_res = monid_service.discover_endpoints(category="jobs", limit=10)
    print(f"Discovered jobs endpoints available: {endpoints_res.get('available')}, total: {endpoints_res.get('total')}")
    if endpoints_res.get("items"):
        sample_ep = endpoints_res["items"][0]
        print(f"Sample Monid Endpoint: {sample_ep.get('provider')}:{sample_ep.get('endpoint')} ({sample_ep.get('displayName')})")
        inspect_res = monid_service.inspect_endpoint(sample_ep.get("provider"), sample_ep.get("endpoint"))
        assert inspect_res.get("found") is True, f"Failed to inspect endpoint: {inspect_res}"
        print("PASS: Monid discovery and inspection verified.")

    with TestClient(app) as client:
        print("\n=== 2. Testing Monid Server-Side Proxy Endpoints ===")
        proxy_resp = client.get("/api/monid/endpoints?category=jobs&limit=5")
        assert proxy_resp.status_code == 200
        proxy_data = proxy_resp.json()
        print(f"Proxy returned {len(proxy_data.get('items', []))} items.")
        print("PASS: Monid secured proxy endpoint verified.")

        print("\n=== 3. Testing Real Job Discovery from Permitted Public Feeds ===")
        # Run discovery for Jobicy, Arbeitnow, and WWR
        disc_resp = client.post("/api/jobs/discover", json={
            "sources": ["jobicy", "weworkremotely", "arbeitnow"],
            "limit": 30,
        })
        assert disc_resp.status_code == 200, f"Discovery request failed: {disc_resp.text}"
        disc_data = disc_resp.json()
        stats = disc_data.get("stats", {})
        print(f"Discovery stats: {stats}")
        assert stats.get("total_fetched", 0) > 0, "No jobs were fetched from public feeds"
        print(f"New jobs added: {stats.get('new_jobs_added')}")
        print("PASS: Public feeds job discovery verified.")

        print("\n=== 4. Testing Duplicate Detection ===")
        # Run discovery again with the exact same sources - new_jobs_added should be 0 or small, and duplicates skipped
        disc_resp_2 = client.post("/api/jobs/discover", json={
            "sources": ["jobicy", "weworkremotely", "arbeitnow"],
            "limit": 30,
        })
        assert disc_resp_2.status_code == 200
        stats_2 = disc_resp_2.json().get("stats", {})
        print(f"Second run stats (duplicate test): {stats_2}")
        assert stats_2.get("duplicates_skipped", 0) > 0, "Deduplication failed to detect existing jobs"
        print("PASS: Duplicate detection verified.")

        print("\n=== 5. Testing Job Listing, Search & Category Filtering ===")
        jobs_resp = client.get("/api/jobs?limit=10")
        assert jobs_resp.status_code == 200
        jobs_data = jobs_resp.json()
        total_jobs = jobs_data["total"]
        jobs_list = jobs_data["jobs"]
        print(f"Total jobs in database: {total_jobs}")
        assert len(jobs_list) > 0, "No jobs returned in list"

        first_job = jobs_list[0]
        print(f"Job sample: [{first_job['status']}] {first_job['job_title']} at {first_job['company']} ({first_job['source']})")
        print(f"Category: {first_job['detected_category']}, Location: {first_job['location']}")

        # Verify all mandatory schema fields are present
        required_fields = [
            "id", "source", "source_job_id", "job_url", "company", "job_title",
            "description", "location", "country", "remote_status", "employment_type",
            "salary", "posted_at", "requirements", "skills", "detected_category",
            "status", "created_at", "updated_at"
        ]
        for field in required_fields:
            assert field in first_job, f"Mandatory field '{field}' missing from Job schema"
        print("PASS: All required job schema fields verified.")

        # Test search query
        search_query = first_job['company'][:4]
        search_resp = client.get(f"/api/jobs?q={search_query}")
        assert search_resp.status_code == 200
        assert search_resp.json()["total"] >= 1
        print("PASS: Job search filter verified.")

        print("\n=== 6. Testing 8-Point Job Verification Engine ===")
        # Run verification on single job
        verify_resp = client.post(f"/api/jobs/{first_job['id']}/verify")
        assert verify_resp.status_code == 200, f"Verification failed: {verify_resp.text}"
        verify_data = verify_resp.json()
        print(f"Verification result: Status={verify_data['status']}, Confidence={verify_data['confidence']}")
        print(f"Summary: {verify_data['summary']}")
        assert verify_data["status"] in ("VERIFIED", "UNVERIFIED", "REJECTED")

        # Verify all 8 checks are reported
        checks = verify_data["checks"]
        expected_checks = [
            "valid_url",
            "identifiable_company",
            "duplicate_status",
            "job_availability",
            "suspicious_indicators",
            "location_compatibility",
            "employment_type",
            "job_age",
        ]
        for ec in expected_checks:
            assert ec in checks, f"Check '{ec}' missing from verification result"
            print(f"  - {ec}: passed={checks[ec].get('passed')}, score={checks[ec].get('score')}")
        print("PASS: All 8 verification checks verified.")

        # Test batch verification
        print("\n=== 7. Testing Batch Verification ===")
        batch_resp = client.post("/api/jobs/verify-batch?limit=5")
        assert batch_resp.status_code == 200
        batch_data = batch_resp.json()
        print(f"Batch verified {batch_data.get('verified_count')} jobs.")
        print("PASS: Batch verification verified.")

    print("\nALL JOB DISCOVERY AND RESEARCH LAYER TESTS PASSED!")


if __name__ == "__main__":
    run_job_layer_tests()
