import logging
from datetime import datetime, timezone

import httpx

from ..schemas.release_notes import ReleaseNotes
from .base import BaseFormatter

logger = logging.getLogger(__name__)

_SECTION_META = [
    ("features", "🚀 Features"),
    ("bug_fixes", "🐛 Bug Fixes"),
    ("improvements", "🔧 Improvements"),
    ("breaking_changes", "⚠️ Breaking Changes"),
]


class SlackFormatter(BaseFormatter):
    def __init__(self, slack_webhook: str = "", **_: object):
        self.slack_webhook = slack_webhook

    async def write(self, notes: ReleaseNotes) -> str:
        if not self.slack_webhook:
            logger.warning("SLACK_WEBHOOK not set — skipping Slack output")
            return "slack:skipped"

        date_str = datetime.now(timezone.utc).strftime("%B %-d, %Y")
        total = (
            len(notes.features)
            + len(notes.bug_fixes)
            + len(notes.improvements)
            + len(notes.breaking_changes)
        )

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Standup Notes — {date_str}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{total} change{'s' if total != 1 else ''} completed*"},
            },
            {"type": "divider"},
        ]

        for attr, label in _SECTION_META:
            bullets = getattr(notes, attr)
            if not bullets:
                continue
            # Strip trailing (cg_xxxxx) IDs from bullets — not useful in Slack
            clean = [_strip_id(b) for b in bullets]
            text = f"*{label} ({len(clean)})*\n" + "\n".join(f"• {b.lstrip('- ')}" for b in clean)
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})

        async with httpx.AsyncClient() as client:
            response = await client.post(self.slack_webhook, json={"blocks": blocks}, timeout=30)
            response.raise_for_status()
        return "slack"


def _strip_id(bullet: str) -> str:
    import re
    return re.sub(r"\s*\(cg_[a-z0-9]+\)\s*$", "", bullet).strip()
