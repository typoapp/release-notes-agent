"""
ADK tool: post the release notes dict (returned by pipeline_tool) to Slack as Block Kit.
"""
import os
import sys

import httpx
from google.adk.tools import FunctionTool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


async def _post_to_slack(
    version: str,
    features: list[str],
    improvements: list[str],
    bug_fixes: list[str],
    breaking_changes: list[str],
) -> dict:
    """
    Post release notes to Slack using Block Kit formatting.

    Args:
        version: Release version string.
        features: List of feature bullet strings.
        improvements: List of improvement bullet strings.
        bug_fixes: List of bug fix bullet strings.
        breaking_changes: List of breaking change bullet strings.

    Returns:
        {"status": "ok"} on success, {"status": "error", "detail": str} on failure.
    """
    webhook = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook:
        return {"status": "error", "detail": "SLACK_WEBHOOK_URL is not set"}

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📋 Release Notes — {version}"}},
    ]
    for title, bullets in [
        ("🚀 Features", features),
        ("🐛 Bug Fixes", bug_fixes),
        ("✨ Improvements", improvements),
        ("⚠️ Breaking Changes", breaking_changes),
    ]:
        if bullets:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{title}*\n" + "\n".join(bullets),
                },
            })

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(webhook, json={"blocks": blocks}, timeout=30)
            r.raise_for_status()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


slack_tool = FunctionTool(func=_post_to_slack)
