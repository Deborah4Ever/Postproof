"""
This is what makes the build an "agent" other agents can call, the same
way the Financial Datasets clone exposes its data as MCP tools instead
of just a webpage. One tool, wrapping the same pipeline your REST
endpoint uses - no separate logic to keep in sync.
"""

from mcp.server.fastmcp import FastMCP

from .pipeline import check_one_posting

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
def check_job_posting(title: str, company: str, description: str = "", posted_at: str = "") -> dict:
    """
    Check whether a job posting is real or a likely ghost job.

    Args:
        title: The job title as posted.
        company: The company name as posted.
        description: The full posting text, if available.
        posted_at: ISO 8601 date the posting went live, if known.

    Returns:
        A dict with verdict ("real" | "suspicious" | "ghost"), a
        confidence score, cited evidence, a ready-to-use pitch line
        (or null if the verdict is "ghost"), and contact info if a
        verified hiring manager was found.
    """
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
