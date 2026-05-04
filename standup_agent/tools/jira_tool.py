"""
ADK tool (optional): fetch JIRA tickets fixed in a given version.
Only wire this into the agent if JIRA_URL is set in the environment.
"""
import os
import sys

from google.adk.tools import FunctionTool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from releasenotes.ingestion.jira import JIRAIngestor


async def _fetch_jira_tickets(from_tag: str, to_tag: str) -> dict:
    """
    Fetch JIRA tickets resolved between two version tags.

    Args:
        from_tag: Start version / date boundary (passed directly to JQL).
        to_tag: End version / date boundary.

    Returns:
        {"tickets": [{"id": str, "title": str, "type": str, "author": str}]}
    """
    ingestor = JIRAIngestor(
        url=os.getenv("JIRA_URL", ""),
        token=os.getenv("JIRA_TOKEN", ""),
        project=os.getenv("JIRA_PROJECT", ""),
        output_dir=os.getenv("RN_OUTPUT_DIR", "./release-notes"),
    )
    events = await ingestor.fetch(from_tag, to_tag)
    tickets = [
        {
            "id": e.source_id,
            "title": e.title,
            "type": e.issue_type or "unknown",
            "author": e.author_name,
        }
        for e in events
        if e.source_type == "ticket"
    ]
    return {"tickets": tickets}


jira_tool = FunctionTool(func=_fetch_jira_tickets)
