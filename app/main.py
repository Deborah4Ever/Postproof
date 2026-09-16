import json
import os
import secrets

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

from .pipeline import scrape_postings, check_one_posting, extract_posting_from_url
from .monid_client import total_measured_cost
from .mcp_server import mcp_app
from .trials import get_trial_count, increment_trial, TRIAL_CAP

app = FastAPI(title="Postproof")

TRIAL_COOKIE_NAME = "trial_id"


def _get_trial_id(request: Request) -> str:
    return request.cookies.get(TRIAL_COOKIE_NAME) or secrets.token_hex(16)


def _set_trial_cookie(response: Response, trial_id: str):
    response.set_cookie(
        TRIAL_COOKIE_NAME, trial_id,
        max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax",
    )


def _trial_exhausted_response(checks_used: int) -> JSONResponse:
    return JSONResponse(status_code=402, content={
        "error": "trial_exhausted",
        "message": "Free trial used up (2 checks) - connect your own Monid key to continue",
        "checks_used": checks_used,
    })

# Same pattern as the Financial Datasets clone: REST and MCP are two
# doors into the same pipeline, served from one process. Any MCP
# client (Claude, Cursor, another agent) can point at /mcp directly.
app.mount("/mcp", mcp_app)

RESULTS_PATH = "receipts/results.jsonl"


class CheckJobRequest(BaseModel):
    url: str | None = None
    title: str | None = None
    company: str | None = None
    description: str = ""
    posted_at: str | None = None

    @model_validator(mode="after")
    def require_url_or_fields(self):
        if not self.url and not (self.title and self.company):
            raise ValueError("Provide either 'url', or both 'title' and 'company'.")
        return self


@app.post("/check-job")
def check_job(req: CheckJobRequest, request: Request, response: Response):
    """
    The one clean endpoint - anyone (or any agent) can POST a single
    posting here and get a verdict back. This is the piece worth
    exposing beyond just your own UI.

    Accepts either a `url` to the live posting (fetched and extracted
    automatically) or the structured fields directly, unchanged from before.

    Free trial: 2 checks per trial_id (cookie-tracked, not IP - Railway's
    proxy makes IP tracking unreliable). A caller who supplies their own
    key via X-User-Monid-Key bypasses the cap entirely and never
    increments the counter - they're spending their own money now.
    """
    trial_id = _get_trial_id(request)
    user_key = request.headers.get("X-User-Monid-Key") or None

    if not user_key:
        used = get_trial_count(trial_id)
        if used >= TRIAL_CAP:
            _set_trial_cookie(response, trial_id)
            return _trial_exhausted_response(used)

    if req.url:
        posting = extract_posting_from_url(req.url, user_key=user_key)
        if posting.get("failed"):
            raise HTTPException(
                status_code=422,
                detail=f"could not extract job posting from URL: {posting['error']}",
            )
    else:
        posting = req.model_dump(exclude={"url"})

    try:
        verdict = check_one_posting(posting, user_key=user_key)
    except Exception as e:
        # Do NOT let one bad posting 500 silently - the guide explicitly
        # requires demoing failure handling, not just the happy path.
        raise HTTPException(status_code=502, detail=f"pipeline failed: {e}")
    _append_result(verdict)

    if not user_key:
        increment_trial(trial_id)

    _set_trial_cookie(response, trial_id)
    verdict["trial"] = {
        "checks_used": get_trial_count(trial_id),
        "cap": TRIAL_CAP,
        "using_user_key": bool(user_key),
    }
    return verdict


@app.post("/run-batch")
def run_batch(role: str, location: str, request: Request, response: Response, limit: int = 10):
    """
    Convenience route for the demo: scrape a fresh batch and check
    every posting in one call, so you're not hand-POSTing 10 times
    on camera.

    Same free-trial cap as /check-job: 2 checks per trial_id unless a
    caller-supplied X-User-Monid-Key is present. If the trial is already
    exhausted before the batch starts, refuse up front. If it runs out
    partway through (shared cookie hitting the cap mid-loop), stop
    checking further postings rather than quietly spending past the cap.
    """
    trial_id = _get_trial_id(request)
    user_key = request.headers.get("X-User-Monid-Key") or None

    if not user_key:
        used = get_trial_count(trial_id)
        if used >= TRIAL_CAP:
            _set_trial_cookie(response, trial_id)
            return _trial_exhausted_response(used)

    cost_before = total_measured_cost()
    postings = scrape_postings(role, location, limit, user_key=user_key)
    results = []
    trial_exhausted_mid_batch = False
    for posting in postings:
        if not user_key and get_trial_count(trial_id) >= TRIAL_CAP:
            trial_exhausted_mid_batch = True
            break
        try:
            verdict = check_one_posting(posting, user_key=user_key)
            results.append(verdict)
            _append_result(verdict)
            if not user_key:
                increment_trial(trial_id)
        except Exception as e:
            results.append({"title": posting.get("title"), "error": str(e)})
    cost_after = total_measured_cost()
    _set_trial_cookie(response, trial_id)
    payload = {
        "checked": len(postings),
        "results": results,
        # marginal spend from this call alone, not the running total -
        # named total_cost_usd before, which read as "this batch cost $X"
        # when it was actually the cumulative ledger total.
        "batch_cost_usd": round(cost_after - cost_before, 6),
        "total_cost_usd": cost_after,
    }
    if trial_exhausted_mid_batch:
        payload["trial_exhausted"] = True
        payload["message"] = "Free trial used up (2 checks) partway through this batch - connect your own Monid key to continue"
    return payload


@app.get("/trial-status")
def trial_status(request: Request, response: Response):
    trial_id = _get_trial_id(request)
    _set_trial_cookie(response, trial_id)
    return {"checks_used": get_trial_count(trial_id), "cap": TRIAL_CAP}


@app.get("/cost")
def cost():
    return {"total_measured_cost_usd": round(total_measured_cost(), 4)}


@app.get("/results")
def results():
    if not os.path.exists(RESULTS_PATH):
        return []
    rows = []
    with open(RESULTS_PATH) as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


@app.get("/", response_class=HTMLResponse)
def feed_page():
    return FileResponse("static/index.html")


@app.get("/llms.txt", response_class=HTMLResponse)
def llms_txt():
    return FileResponse("static/llms.txt", media_type="text/plain")


def _append_result(verdict: dict):
    os.makedirs("receipts", exist_ok=True)
    with open(RESULTS_PATH, "a") as f:
        f.write(json.dumps(verdict) + "\n")


app.mount("/static", StaticFiles(directory="static"), name="static")
