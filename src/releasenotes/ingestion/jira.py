import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

import httpx
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..exceptions import IngestionError
from ..schemas.change_event import ChangeEvent
from .base import BaseIngestor

logger = logging.getLogger(__name__)


ISSUE_TYPE_MAP = {
    "bug": "bug",
    "defect": "bug",
    "hotfix": "bug",
    "story": "story",
    "feature": "story",
    "epic": "story",
    "task": "task",
    "sub-task": "task",
    "improvement": "task",
    "chore": "task",
}


class JIRAIngestor(BaseIngestor):
    def __init__(
        self,
        url: str = "",
        token: str = "",
        project: str = "",
        output_dir: str = "./release-notes",
        email: str = "",
        **_: Any,
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.project = project
        self.output_dir = output_dir
        self.auth = (email, token) if email else None
        self.headers = {"Accept": "application/json"}
        if token and not email:
            self.headers["Authorization"] = f"Bearer {token}"

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def _request(self, client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
        response = await client.request(
            method, url, headers=self.headers, auth=self.auth, timeout=30, **kwargs
        )
        if response.status_code == 429 or response.status_code >= 500:
            response.raise_for_status()
        if response.status_code >= 400:
            raise IngestionError("jira", f"HTTP {response.status_code}: {response.text[:200]}")
        return response

    async def health_check(self) -> bool:
        if not self.url:
            return False
        try:
            async with httpx.AsyncClient() as client:
                response = await self._request(client, "GET", f"{self.url}/rest/api/3/myself")
            return response.status_code == 200
        except Exception:
            return False

    async def fetch(self, from_ref: str, to_ref: str) -> list[ChangeEvent]:
        if not self.url or not self.project:
            raise IngestionError("jira", "jira_url and jira_project are required")
        if not _DATE_RE.match(from_ref):
            logger.warning(
                "JIRA ingestor requires date-based references (e.g. --since 24h). "
                "Got tag '%s' — skipping JIRA ingestion.",
                from_ref,
            )
            return []
        since_str = _jira_date(from_ref)
        jql = (
            f"project = {self.project} "
            f'AND status in (Done, Closed, Resolved) '
            f'AND updated >= "{since_str}" '
            f"ORDER BY updated DESC"
        )
        try:
            events: list[ChangeEvent] = []
            start_at = 0
            page = 1
            total = 1
            async with httpx.AsyncClient() as client:
                while start_at < total:
                    response = await self._request(
                        client,
                        "GET",
                        f"{self.url}/rest/api/3/search/jql",
                        params={
                            "jql": jql,
                            "startAt": start_at,
                            "maxResults": 100,
                            "fields": "summary,description,issuetype,status,labels,priority,assignee,creator,created,updated",
                        },
                    )
                    payload = response.json()
                    _write_raw_page(self.output_dir, "jira", page, payload)
                    total = int(payload.get("total", 0))
                    issues = payload.get("issues", [])
                    events.extend(_issue_event(issue) for issue in issues)
                    start_at += len(issues) or 100
                    page += 1
            _write_checkpoint(self.output_dir, "jira", to_ref)
            return events
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError("jira", str(exc)) from exc


def _jira_date(iso_str: str) -> str:
    """Convert ISO 8601 string to JIRA JQL date format (yyyy-MM-dd HH:mm)."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso_str[:10]


def adf_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "text" in node:
                parts.append(str(node["text"]))
            for child in node.get("content", []):
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return " ".join(part.strip() for part in parts if part.strip())


def _status(value: str) -> str:
    lowered = value.lower()
    if lowered in {"done", "closed", "resolved"}:
        return "done"
    if lowered in {"in progress", "in_progress", "selected for development"}:
        return "in_progress"
    if lowered in {"wont fix", "won't fix", "rejected"}:
        return "wont_fix"
    return "open"


def _event_id() -> str:
    return f"ce_{uuid4().hex[:8]}"


def _write_checkpoint(output_dir: str, source: str, to_tag: str) -> None:
    path = Path(output_dir) / ".releasenotes_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            data = {}
    data[source] = {"last_tag": to_tag, "last_run": datetime.now().astimezone().isoformat()}
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def _write_raw_page(output_dir: str, source: str, page: int, payload: Any) -> None:
    date = datetime.now().astimezone().date().isoformat()
    root = Path(output_dir) / ".raw" / source / date
    root.mkdir(parents=True, exist_ok=True)
    (root / f"page_{page}.json").write_text(json.dumps(payload, indent=2, default=str))


def _issue_event(issue: dict[str, Any]) -> ChangeEvent:
    fields = issue.get("fields", {})
    assignee = fields.get("assignee") or fields.get("creator") or {}
    issue_type = fields.get("issuetype", {}).get("name", "")
    priority = fields.get("priority") or {}
    labels = fields.get("labels") or []
    return ChangeEvent(
        id=_event_id(),
        source_type="ticket",
        source_id=issue.get("key", ""),
        source_system="jira",
        title=fields.get("summary") or "",
        body=adf_to_text(fields.get("description")),
        author_name=assignee.get("displayName") or "unknown",
        author_email=(assignee.get("emailAddress") or "").lower(),
        created_at=fields.get("created") or "",
        updated_at=fields.get("updated") or "",
        status=_status(fields.get("status", {}).get("name", "")),
        raw_labels=labels,
        normalized_labels=[label.lower().strip() for label in labels],
        priority=(priority.get("name") or "").lower() or None,
        issue_type=ISSUE_TYPE_MAP.get(issue_type.lower(), issue_type.lower() or None),
        raw_payload=issue,
    )
