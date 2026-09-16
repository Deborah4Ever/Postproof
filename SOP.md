# SOP — Verified Jobs Feed (for Deborah)

Kills Contena's $99/mo writer job board by checking whether a posting —
and the hiring manager behind it — is real, before pitching or placing
into it. Works for both writer gigs and recruiter reqs; same pipeline.

---

## Phase 0 — Access handoff (Deborah's part, do this first)

Nothing downstream works without these three things, in this order:

1. **GitHub** — Deborah creates an empty repo under her own account
   (e.g. `deborah-username/verified-jobs`) and adds you as a
   collaborator. Don't push to a repo under your account and move it
   later — start it in the right place.
2. **Monid key** — Deborah shares the key value directly (voice call
   or password manager share, not a message that sits in chat history
   indefinitely). Goes straight into your local `.env`, never committed.
3. **Railway** — Deborah either runs the deploy commands herself once
   the code is ready, or adds you as a collaborator on her Railway
   project so `railway up` deploys under her account, not a new one.

**Before going further: confirm with Deborah whether this is going
toward the actual Monid hackathon submission.** If so, check the
hackathon's team-submission rules now — attribution and registration
need to be sorted before the build is deep, not after.

---

## Phase 1 — Local setup (15-20 min)

```bash
git clone https://github.com/<deborah-account>/verified-jobs.git
cd verified-jobs
```

Unzip the scaffold into this folder (or have it already there if
pushed from the phone session). Confirm `.env` is in `.gitignore`
before you put real keys anywhere near this folder.

```bash
cp .env.example .env
# fill in MONID_API_KEY and ANTHROPIC_API_KEY with Deborah's values
pip install -r requirements.txt
```

**Checkpoint:** `pip install` finishes clean, no errors. If it
doesn't, stop and fix here — nothing after this works otherwise.

---

## Phase 2 — Confirm real Monid tools (30-45 min, don't skip)

```bash
monid discover jobs
monid discover "web search"
monid discover "people search"
monid discover apollo
```

Fill in the four `TOOL_*` placeholder constants at the top of
`app/monid_client.py` with whatever `monid discover` actually returns.
Then `monid inspect <tool_id>` on one of them and check the real
response shape against `call_monid_tool()` in that same file — fix the
`cost_usd` / `run_id` / `data` field names if they don't match.

**Checkpoint:** run one real call by hand and confirm you get back a
non-zero cost:
```python
from app.monid_client import call_monid_tool
r = call_monid_tool("<a real tool id>", {"query": "test"})
print(r.data, r.cost_usd)
```
If `cost_usd` is `0.0`, the field names are still wrong — fix before
moving on, this is the step most likely to eat time if rushed.

---

## Phase 3 — One full round-trip (15-20 min)

```python
from app.pipeline import check_one_posting
check_one_posting({"title": "Content Writer", "company": "<a real company you know is hiring>", "description": "..."})
```

**Checkpoint:** open `receipts/ledger.jsonl` — you should see several
real, non-zero cost lines (one per Monid call in the chain). If this
works, the hard part is done.

---

## Phase 4 — Run it locally (10 min)

```bash
uvicorn app.main:app --reload
```

Visit `http://localhost:8000` — the live feed page loads (empty table
at first). Test the batch route:
```bash
curl -X POST "localhost:8000/run-batch?role=content%20writer&location=remote&limit=5"
```

**Checkpoint:** the table on `/` fills in within a few seconds, cost
counter ticks up from $0.00.

---

## Phase 5 — Verdict quality pass (45-60 min, worth the time)

Test `check_one_posting` against a couple of postings you already know
are ghost jobs and a couple you know are real. Adjust `VERDICT_PROMPT`
in `app/verdict.py` until it's calling them correctly. This step is
the actual product — don't shortchange it to save time elsewhere.

---

## Phase 6 — Deploy (30-45 min)

Either Deborah runs this, or you do it with collaborator access:
```bash
railway login
railway init
railway up
railway variables set MONID_API_KEY=... ANTHROPIC_API_KEY=...
```

**Checkpoint:** the deployed URL loads the same feed page, `/cost` and
`/check-job` respond correctly from outside your machine.

---

## Phase 7 — Prove it's an agent, not just an API (already built in)

- `/mcp` — MCP server exposing `check_job_posting` as a callable tool
- `/llms.txt` — plain-text API description for any LLM to read
- The onboarding prompt in `README.md` — paste into any agent to
  configure it against this deployment

For the demo: show `/check-job` called once from your own feed page,
and once from a raw MCP client — same tool, two callers, proving it
isn't locked to your own UI.

---

## Phase 8 — Record and ship

1. Run a real batch on camera (`role=content writer`), let the table
   fill in live, let the cost counter tick up on screen
2. Show one failure case handled cleanly (empty scrape, no company
   match) — not hidden, shown
3. State plainly what's cut for time: schema.org compatibility,
   accuracy benchmark — roadmap, not hidden gaps
4. Receipt on screen: total from `/cost` vs. Contena's $99/month
5. Confirm with Deborah how the submission gets credited before
   posting anywhere

---

## If something breaks mid-build
Come back here and say exactly which Phase/Checkpoint failed and what
error you got — don't restart from Phase 1 unless the checkpoint
before it also failed.
