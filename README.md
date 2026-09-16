# Postproof

Checks whether a job posting - and the human behind it - is real before
you pitch or place someone into it. Kills Contena's $99/mo writer job
board, which aggregates postings but never verifies them.

## The 5-hour build order

**Do not skip step 0.** Everything downstream depends on real Monid tool
names, and guessing them wastes far more time than confirming them up
front.

### 0. Confirm real Monid tool names (30-45 min)
```
monid discover jobs
monid discover "web search"
monid discover "people search"
monid discover apollo
```
Fill in the four `TOOL_*` constants at the top of `app/monid_client.py`
with whatever `monid discover` actually returns. Also check the shape
of one real response with `monid inspect <tool_id>` and fix the field
names in `call_monid_tool()` (`cost_usd`, `run_id`, `data`) to match -
this file currently guesses at Monid's response shape.

### 1. One hardcoded round trip (30 min)
Before touching FastAPI, run this in a plain Python shell to prove the
chain works on one real posting:
```python
from app.pipeline import check_one_posting
check_one_posting({"title": "Content Writer", "company": "Acme Inc", "description": "..."})
```
Check `receipts/ledger.jsonl` afterward - you should see real cost
lines, not zeros.

### 2. Wire the FastAPI app (30 min)
Already scaffolded in `app/main.py`. Run it:
```bash
pip install -r requirements.txt
export MONID_API_KEY=...
export ANTHROPIC_API_KEY=...
uvicorn app.main:app --reload
```
Visit `http://localhost:8000` for the live table, or:
```bash
curl -X POST localhost:8000/run-batch -d '{"role":"content writer","location":"remote"}' -H 'Content-Type: application/json'
```

### 3. Verdict prompt tuning (45-60 min)
This is the part that has to look like judgment, not a lookup table -
spend real time here. Test it against a few postings you already know
are ghost jobs and a few you know are real, and adjust `VERDICT_PROMPT`
in `app/verdict.py` until it's calling them correctly.

### 4. Deploy (30-45 min)
```bash
railway login
railway init
railway up
railway variables set MONID_API_KEY=... ANTHROPIC_API_KEY=...
```

### 5. Run a real batch + record (remaining time)
Hit `/run-batch` with a real role/location, let the table fill in live,
screen-record the cost counter ticking up. That's your 90-second shot.

## Making it an agent, not just an API (added after v1)

This is what made the Financial Datasets clone read as agent-native
rather than "just a REST wrapper": an MCP server exposing the same
logic as a callable tool, plus a plain-text description any LLM can
read without a human writing integration code.

- `app/mcp_server.py` - one MCP tool, `check_job_posting`, mounted at
  `/mcp` alongside the REST routes in the same FastAPI process. Point
  any MCP client (Claude, Cursor, another agent) at
  `https://<your-deploy>/mcp` and it can call this directly.
- `static/llms.txt` - served at `/llms.txt`, describes every endpoint
  in plain text so an agent can read the whole API surface in one shot.
- **Onboarding prompt** (paste this into any agent's chat to configure
  it, same move as Financial Datasets' "copy onboarding prompt" button):

  > Use Postproof at https://<your-deploy> to check whether
  > a job posting is real before applying, pitching, or placing someone
  > into it. Call POST /check-job with {title, company, description}, or
  > connect via MCP at /mcp using the check_job_posting tool. Full API
  > reference at /llms.txt.

## What's still deliberately NOT in this build
- schema.org JobPosting compatibility - would make this a drop-in for
  tools built against that standard, cut for time
- Hand-verified accuracy benchmark - do this only if time remains after
  everything else works

Say both out loud in the video as honest scoping, not hidden gaps -
and say the MCP/llms.txt layer out loud too, since it's the thing that
answers "would another agent builder reach for this tomorrow."
