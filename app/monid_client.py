"""
Thin wrapper around Monid's HTTP API.

BEFORE YOU DO ANYTHING ELSE:
Run `monid discover` and `monid inspect <tool>` for these categories and
fill in the real tool/endpoint names below. Do not guess at names -
this file has placeholders where the real Monid tool identifiers go.

    - job scraping (LinkedIn/Indeed via Apify route)
    - web search (Octen or Exa) for company legitimacy
    - LinkedIn people search / PDL for hiring-manager existence
    - Apollo enrichment for verified contact email

Every call MUST go through `call_monid_tool` below so cost logging
is never bypassed - that's your entire "measured, not estimated" story.
"""

import os
import time
import httpx

MONID_API_KEY = os.environ["MONID_API_KEY"]
MONID_BASE_URL = os.environ.get("MONID_BASE_URL", "https://api.monid.ai/v1")

# Real tool IDs confirmed via `monid discover` + `monid inspect` 2026-09-15
# Job scraping: Wellfound — stable 1.7s, $0.03/call, verified
TOOL_JOB_SCRAPE = "wellfound:/search_jobs"
# Company legitimacy: Surf web search — stable ~4s, $0.006/call, verified
TOOL_COMPANY_SEARCH = "surf:/search/web"
# Hiring-manager existence: Apollo people search — free ($0/call), healthy 1.7s
TOOL_PEOPLE_SEARCH = "apollo:/mixed_people/api_search"
# Contact enrichment: Apollo people match — $0.05/call, stable 1.9s
TOOL_ENRICHMENT = "apollo:/people/match"

_client = httpx.Client(
    base_url=MONID_BASE_URL,
    headers={
        "Authorization": f"Bearer {MONID_API_KEY}",  # real header; X-API-KEY is wrong
        "Content-Type": "application/json",
    },
    timeout=60.0,
)


class MonidCallResult:
    def __init__(self, tool: str, data: dict, cost_usd: float, run_id: str | None, success: bool = True):
        self.tool = tool
        self.data = data
        self.cost_usd = cost_usd
        self.run_id = run_id
        self.success = success


def _parse_tool_id(tool_id: str) -> tuple[str, str]:
    """Split 'provider:/endpoint' into (provider, endpoint)."""
    provider, endpoint = tool_id.split(":", 1)
    return provider, endpoint


def _poll_until_done(run_id: str, max_wait: int = 120) -> dict:
    """Poll GET /runs/{runId} until status != RUNNING (for async providers)."""
    import time as _time
    deadline = _time.time() + max_wait
    while _time.time() < deadline:
        resp = _client.get(f"/runs/{run_id}")
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "RUNNING":
            return body
        _time.sleep(3)
    raise TimeoutError(f"Monid run {run_id} still RUNNING after {max_wait}s")


def call_monid_tool(tool_id: str, params: dict) -> MonidCallResult:
    """
    Calls a Monid tool and returns the result plus the REAL measured cost
    Monid reports for that call. Never estimate cost - always read it from
    the response.

    Real Monid HTTP API (confirmed via docs + live call 2026-09-15):
      POST /v1/run
      Body: { "provider": "surf", "endpoint": "/search/web", "input": { ... } }
      Auth: Authorization: Bearer <key>   (NOT X-API-KEY)

    Real response shape - CONFIRMED VIA LIVE CALLS 2026-09-15 THAT THIS IS
    NOT UNIFORM ACROSS PROVIDERS. Two shapes exist:

    Shape A (surf, apollo/mixed_people_search - confirmed live):
      {
        "runId": "01M2...", "status": "COMPLETED", "output": {...},
        "billing": {
          "reportedCost": {"value": 6000, "unit": "MICRO_DOLLAR", "currency": "USD"}
        }
      }
      No top-level "cost" key in this shape.

    Shape B (wellfound, apollo/people_match - confirmed live):
      {
        "runId": "01M2...", "status": "COMPLETED", "output": {...},
        "cost": {"value": 0.03, "currency": "USD"}   <- already whole USD, NOT micro-dollars
      }
      No "billing" key at all in this shape - reading billing.reportedCost.value
      here silently defaults to 0, which is why wellfound/people_match logged
      $0.0 for every call until this was caught.

    So: prefer top-level "cost.value" (already USD) when present, else fall
    back to billing.reportedCost.value / 1_000_000.

    "Show your real cost including failed runs" means this function must
    never let a 4xx/5xx/timeout escape uncaught without logging the attempt
    first - callers get back a MonidCallResult with success=False instead
    of an exception, so one bad tool call doesn't crash the whole batch.
    """
    provider, endpoint = _parse_tool_id(tool_id)
    run_id = None
    try:
        # Confirmed via live test 2026-09-15: all four endpoints use queryParams,
        # not 'input'. Using 'input: {q: ...}' caused 400 "received undefined".
        resp = _client.post(
            "/run",
            json={"provider": provider, "endpoint": endpoint, "queryParams": params},
        )
        resp.raise_for_status()  # 200 sync, 202 async-accepted are both OK; raises on 4xx/5xx
        body = resp.json()

        # 202 means the run was accepted but is RUNNING (async providers like apollo /people/match).
        # The initial body contains runId — poll until COMPLETED.
        run_id = body.get("runId")
        if body.get("status") == "RUNNING" and run_id:
            body = _poll_until_done(run_id)

        # Real field names from confirmed live response:
        run_id = body.get("runId") or run_id                # camelCase, not snake_case
        cost = _extract_cost(body)
        data = body.get("output", body)                     # payload is under "output"

        log_receipt(tool=tool_id, params=params, cost_usd=cost, run_id=run_id, status="completed")
        return MonidCallResult(tool=tool_id, data=data, cost_usd=cost, run_id=run_id, success=True)
    except Exception as exc:
        # Monid sometimes bills a run that started and then failed, so pull
        # whatever cost/runId info is in the error response before giving up -
        # only default to 0.0/None when there's genuinely nothing to read
        # (e.g. a connection error with no response at all).
        cost = 0.0
        error_body = None
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                error_body = response.json()
            except ValueError:
                error_body = None
        if error_body:
            run_id = error_body.get("runId") or run_id
            cost = _extract_cost(error_body)

        log_receipt(tool=tool_id, params=params, cost_usd=cost, run_id=run_id, status="failed")
        return MonidCallResult(tool=tool_id, data={}, cost_usd=cost, run_id=run_id, success=False)


def _extract_cost(body: dict) -> float:
    """Monid's cost field isn't uniform across providers - see call_monid_tool docstring."""
    top_level_cost = body.get("cost")
    if top_level_cost is not None:
        return top_level_cost.get("value", 0)            # shape B: already whole USD
    micro_dollars = (
        body.get("billing", {})
            .get("reportedCost", {})
            .get("value", 0)
    )
    return micro_dollars / 1_000_000                      # shape A: micro-USD -> USD


def log_receipt(tool: str, params: dict, cost_usd: float, run_id: str | None, status: str):
    import json

    receipts_path = os.environ.get("RECEIPTS_PATH", "receipts/ledger.jsonl")
    os.makedirs(os.path.dirname(receipts_path) or ".", exist_ok=True)
    line = {
        "ts": time.time(),
        "tool": tool,
        "run_id": run_id,
        "cost_usd": cost_usd,
        "status": status,
    }
    with open(receipts_path, "a") as f:
        f.write(json.dumps(line) + "\n")


def total_measured_cost() -> float:
    import json

    receipts_path = os.environ.get("RECEIPTS_PATH", "receipts/ledger.jsonl")
    if not os.path.exists(receipts_path):
        return 0.0
    total = 0.0
    with open(receipts_path) as f:
        for line in f:
            if line.strip():
                total += json.loads(line)["cost_usd"]
    return total
