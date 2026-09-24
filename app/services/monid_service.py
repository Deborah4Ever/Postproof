"""
Dedicated Monid Service Module.

Provides comprehensive integration with Monid's HTTP API:
1. Endpoint discovery
2. Endpoint inspection
3. Endpoint execution
4. Result retrieval (sync and async polling)
5. Robust error handling
6. Configurable timeout handling
7. Accurate cost tracking (micro-dollar to USD and direct USD conversions)

Security: MONID_API_KEY is read strictly from environment variables and is
NEVER sent or exposed to the client frontend.
"""

import os
import time
import httpx
from typing import Any
from dotenv import load_dotenv

load_dotenv()


class MonidConfigError(Exception):
    """Raised when required Monid credentials or configuration are missing."""
    pass


class MonidTimeoutError(Exception):
    """Raised when an asynchronous run exceeds the maximum wait duration."""
    pass


class MonidExecutionResult:
    """Represents the standardized result of a Monid endpoint execution."""
    def __init__(
        self,
        provider: str,
        endpoint: str,
        data: Any,
        cost_usd: float,
        run_id: str | None,
        success: bool = True,
        error: str | None = None,
        raw_response: dict | None = None,
    ):
        self.provider = provider
        self.endpoint = endpoint
        self.tool_id = f"{provider}:{endpoint}"
        self.data = data
        self.cost_usd = round(cost_usd, 6)
        self.run_id = run_id
        self.success = success
        self.error = error
        self.raw_response = raw_response or {}

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "endpoint": self.endpoint,
            "tool_id": self.tool_id,
            "success": self.success,
            "cost_usd": self.cost_usd,
            "run_id": self.run_id,
            "error": self.error,
            "data": self.data,
        }


class MonidService:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.environ.get("MONID_API_KEY")
        self.base_url = (base_url or os.environ.get("MONID_BASE_URL", "https://api.monid.ai/v1")).rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("your-"))

    def _get_headers(self, user_override_key: str | None = None) -> dict[str, str]:
        key = user_override_key or self.api_key
        if not key or key.startswith("your-"):
            raise MonidConfigError("MONID_API_KEY environment variable is missing or placeholder.")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "Personal-AI-Job-Agent/1.0",
        }

    # ------------------------------------------------------------------
    # 1. Endpoint Discovery
    # ------------------------------------------------------------------
    def discover_endpoints(
        self,
        category: str | None = None,
        provider: str | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """
        Discovers available tools/endpoints in Monid catalog according to
        category (e.g. 'jobs', 'seo', 'enrichment', 'search'), provider, or query.
        """
        if not self.is_configured():
            return {
                "available": False,
                "error": "MONID_API_KEY is not configured.",
                "total": 0,
                "items": [],
            }

        params: dict[str, Any] = {}
        if category:
            params["category"] = category
        if provider:
            params["provider"] = provider

        try:
            with httpx.Client(base_url=self.base_url, headers=self._get_headers(), timeout=15.0) as client:
                resp = client.get("/endpoints", params=params)
                resp.raise_for_status()
                payload = resp.json()
                items = payload.get("items", [])

                # Client-side text filter if query keyword provided
                if query:
                    q_lower = query.lower()
                    items = [
                        it for it in items
                        if q_lower in it.get("displayName", "").lower()
                        or q_lower in it.get("displayDescription", "").lower()
                        or q_lower in it.get("provider", "").lower()
                        or q_lower in it.get("endpoint", "").lower()
                    ]

                return {
                    "available": True,
                    "total": len(items),
                    "items": items[:limit],
                    "cursor": payload.get("cursor"),
                }
        except httpx.HTTPStatusError as e:
            return {
                "available": False,
                "error": f"Monid API error ({e.response.status_code}): {e.response.text}",
                "total": 0,
                "items": [],
            }
        except Exception as e:
            return {
                "available": False,
                "error": f"Failed to connect to Monid: {str(e)}",
                "total": 0,
                "items": [],
            }

    # ------------------------------------------------------------------
    # 2. Endpoint Inspection
    # ------------------------------------------------------------------
    def inspect_endpoint(self, provider: str, endpoint: str) -> dict[str, Any]:
        """
        Retrieves detailed metadata, pricing, description, and categories
        for a specific Monid provider endpoint.
        """
        if not self.is_configured():
            return {"found": False, "error": "MONID_API_KEY is not configured."}

        try:
            with httpx.Client(base_url=self.base_url, headers=self._get_headers(), timeout=15.0) as client:
                resp = client.get("/endpoints", params={"provider": provider, "endpoint": endpoint})
                resp.raise_for_status()
                items = resp.json().get("items", [])
                match = next((i for i in items if i.get("provider") == provider and i.get("endpoint") == endpoint), None)
                if match:
                    return {"found": True, "endpoint": match}
                elif items:
                    return {"found": True, "endpoint": items[0]}
                return {"found": False, "error": f"Endpoint '{provider}:{endpoint}' not found."}
        except Exception as e:
            return {"found": False, "error": f"Inspection failed: {str(e)}"}

    # ------------------------------------------------------------------
    # 3. Endpoint Execution & 4. Result Retrieval
    # ------------------------------------------------------------------
    def execute_endpoint(
        self,
        provider: str,
        endpoint: str,
        query_params: dict[str, Any] | None = None,
        user_override_key: str | None = None,
        timeout_seconds: float = 60.0,
        poll_max_wait: int = 120,
    ) -> MonidExecutionResult:
        """
        Executes a Monid tool endpoint.
        Handles both synchronous responses and asynchronous 202 RUNNING jobs
        by polling /runs/{runId} until completion or timeout.
        """
        if not self.is_configured() and not user_override_key:
            return MonidExecutionResult(
                provider=provider,
                endpoint=endpoint,
                data={},
                cost_usd=0.0,
                run_id=None,
                success=False,
                error="MONID_API_KEY is not configured.",
            )

        headers = self._get_headers(user_override_key)
        payload = {
            "provider": provider,
            "endpoint": endpoint,
            "queryParams": query_params or {},
        }

        try:
            with httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout_seconds) as client:
                resp = client.post("/run", json=payload)
                resp.raise_for_status()
                body = resp.json()

                run_id = body.get("runId")
                status = body.get("status")

                # Asynchronous polling if accepted (202 or status RUNNING)
                if status == "RUNNING" and run_id:
                    body = self._poll_run(client, run_id, max_wait=poll_max_wait)

                cost = self._extract_cost(body)
                data = body.get("output", body)

                self._record_receipt(provider, endpoint, run_id, cost, "completed")
                return MonidExecutionResult(
                    provider=provider,
                    endpoint=endpoint,
                    data=data,
                    cost_usd=cost,
                    run_id=run_id,
                    success=True,
                    raw_response=body,
                )

        except httpx.HTTPStatusError as exc:
            cost = 0.0
            run_id = None
            try:
                err_json = exc.response.json()
                cost = self._extract_cost(err_json)
                run_id = err_json.get("runId")
            except Exception:
                pass
            self._record_receipt(provider, endpoint, run_id, cost, "failed")
            return MonidExecutionResult(
                provider=provider,
                endpoint=endpoint,
                data={},
                cost_usd=cost,
                run_id=run_id,
                success=False,
                error=f"HTTP {exc.response.status_code}: {exc.response.text}",
            )
        except Exception as exc:
            self._record_receipt(provider, endpoint, None, 0.0, "failed")
            return MonidExecutionResult(
                provider=provider,
                endpoint=endpoint,
                data={},
                cost_usd=0.0,
                run_id=None,
                success=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # 5. Polling & Timeout Handling
    # ------------------------------------------------------------------
    def _poll_run(self, client: httpx.Client, run_id: str, max_wait: int = 120, poll_interval: float = 3.0) -> dict:
        deadline = time.time() + max_wait
        while time.time() < deadline:
            resp = client.get(f"/runs/{run_id}")
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") != "RUNNING":
                return body
            time.sleep(poll_interval)
        raise MonidTimeoutError(f"Monid run '{run_id}' exceeded timeout of {max_wait}s.")

    # ------------------------------------------------------------------
    # 6. Cost Tracking
    # ------------------------------------------------------------------
    def _extract_cost(self, body: dict) -> float:
        """
        Standardizes cost extraction across Monid providers:
        - Shape B: top-level {"cost": {"value": 0.03, "currency": "USD"}}
        - Shape A: {"billing": {"reportedCost": {"value": 6000, "unit": "MICRO_DOLLAR"}}}
        """
        top_cost = body.get("cost")
        if isinstance(top_cost, dict) and "value" in top_cost:
            return float(top_cost.get("value", 0.0))

        billing = body.get("billing", {})
        if isinstance(billing, dict):
            reported = billing.get("reportedCost", {})
            if isinstance(reported, dict) and "value" in reported:
                val = float(reported.get("value", 0))
                unit = reported.get("unit", "MICRO_DOLLAR")
                if unit == "MICRO_DOLLAR":
                    return val / 1_000_000.0
                return val

        return 0.0

    def _record_receipt(self, provider: str, endpoint: str, run_id: str | None, cost_usd: float, status: str):
        """Logs execution to the audit ledger."""
        try:
            import json
            receipts_path = os.environ.get("RECEIPTS_PATH", "receipts/ledger.jsonl")
            os.makedirs(os.path.dirname(receipts_path) or ".", exist_ok=True)
            entry = {
                "ts": time.time(),
                "tool": f"{provider}:{endpoint}",
                "run_id": run_id,
                "cost_usd": cost_usd,
                "status": status,
                "source": "server_key",
            }
            with open(receipts_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass


# Singleton instance
monid_service = MonidService()
