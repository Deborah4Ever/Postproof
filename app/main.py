import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .pipeline import scrape_postings, check_one_posting
from .monid_client import total_measured_cost
from .mcp_server import mcp_app

app = FastAPI(title="Verified Jobs Feed")

# Same pattern as the Financial Datasets clone: REST and MCP are two
# doors into the same pipeline, served from one process. Any MCP
# client (Claude, Cursor, another agent) can point at /mcp directly.
app.mount("/mcp", mcp_app)

RESULTS_PATH = "receipts/results.jsonl"


class CheckJobRequest(BaseModel):
    title: str
    company: str
    description: str = ""
    posted_at: str | None = None


@app.post("/check-job")
def check_job(req: CheckJobRequest):
    """
    The one clean endpoint - anyone (or any agent) can POST a single
    posting here and get a verdict back. This is the piece worth
    exposing beyond just your own UI.
    """
    try:
        verdict = check_one_posting(req.model_dump())
    except Exception as e:
        # Do NOT let one bad posting 500 silently - the guide explicitly
        # requires demoing failure handling, not just the happy path.
        raise HTTPException(status_code=502, detail=f"pipeline failed: {e}")
    _append_result(verdict)
    return verdict


@app.post("/run-batch")
def run_batch(role: str, location: str, limit: int = 10):
    """
    Convenience route for the demo: scrape a fresh batch and check
    every posting in one call, so you're not hand-POSTing 10 times
    on camera.
    """
    cost_before = total_measured_cost()
    postings = scrape_postings(role, location, limit)
    results = []
    for posting in postings:
        try:
            verdict = check_one_posting(posting)
            results.append(verdict)
            _append_result(verdict)
        except Exception as e:
            results.append({"title": posting.get("title"), "error": str(e)})
    cost_after = total_measured_cost()
    return {
        "checked": len(postings),
        "results": results,
        # marginal spend from this call alone, not the running total -
        # named total_cost_usd before, which read as "this batch cost $X"
        # when it was actually the cumulative ledger total.
        "batch_cost_usd": round(cost_after - cost_before, 6),
        "total_cost_usd": cost_after,
    }


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
