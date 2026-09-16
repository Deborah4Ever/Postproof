"""
This is what makes the build an "agent" other agents can call, the same
way the Financial Datasets clone exposes its data as MCP tools instead
of just a webpage. One tool, wrapping the same pipeline your REST
endpoint uses - no separate logic to keep in sync.
"""

from mcp.server.fastmcp import FastMCP

from .pipeline import check_one_posting, extract_posting_from_url

mcp = FastMCP(
    name="postproof",
    instructions=(
        "Use check_job_posting to verify whether a job posting is real "
        "before applying, pitching, or placing a candidate into it. "
        "It checks posting freshness, company web/social activity, "
        "whether a real named hiring manager exists for the role, and "
        "returns a verdict (real / suspicious / ghost) with cited "
        "evidence, plus a verified contact if the posting passes."
    ),
)


@mcp.tool()
def check_job_posting(
    url: str = "",
    title: str = "",
    company: str = "",
    description: str = "",
    posted_at: str = "",
) -> dict:
    """
    Check whether a job posting is real or a likely ghost job.

    Provide EITHER `url` (a link to the live posting - LinkedIn, Indeed, a
    company careers page, etc.) OR both `title` and `company` typed
    directly. If `url` is given, the page is fetched and its fields are
    extracted automatically; the other fields are ignored in that case.

    Args:
        url: A link to the live job posting, if you have one.
        title: The job title as posted (required if url is not given).
        company: The company name as posted (required if url is not given).
        description: The full posting text, if available (ignored if url is given).
        posted_at: ISO 8601 date the posting went live, if known (ignored if url is given).

    Returns:
        A dict with verdict ("real" | "suspicious" | "ghost"), a
        confidence score, cited evidence, a ready-to-use pitch line
        (or null if the verdict is "ghost"), and contact info if a
        verified hiring manager was found. If url extraction fails,
        returns {"error": "..."} instead of a verdict.
    """
    if url:
        posting = extract_posting_from_url(url)
        if posting.get("failed"):
            return {"error": f"could not extract job posting from URL: {posting['error']}"}
    else:
        posting = {
            "title": title,
            "company": company,
            "description": description,
            "posted_at": posted_at or None,
        }
    return check_one_posting(posting)


def _sse_app(self):
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from mcp.server.sse import SseServerTransport

    sse = SseServerTransport("/mcp/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await self._mcp_server.run(
                streams[0],
                streams[1],
                self._mcp_server.create_initialization_options(),
            )

    return Starlette(
        debug=self.settings.debug,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )

if not hasattr(FastMCP, "sse_app"):
    FastMCP.sse_app = _sse_app

# Exposed as an ASGI app so main.py can mount it at /mcp, same pattern
# as the Financial Datasets clone serving REST + MCP from one process.
mcp_app = mcp.sse_app()
