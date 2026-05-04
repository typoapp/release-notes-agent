import json
import logging
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..exceptions import IngestionError
from ..schemas.change_event import ChangeEvent
from .base import BaseIngestor

logger = logging.getLogger(__name__)
CONVENTIONAL_RE = re.compile(r"^(?P<type>feat|fix|chore|refactor|perf|docs|test|breaking)(\(.+\))?(!)?:\s")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


class GitHubIngestor(BaseIngestor):
    def __init__(
        self,
        token: str = "",
        repo: str = "",
        output_dir: str = "./release-notes",
        fetch_diffs: bool = False,
        base_url: str = "https://api.github.com",
        **_: Any,
    ):
        self.token = token
        self.repo = repo
        self.output_dir = output_dir
        self.fetch_diffs = fetch_diffs
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def _request(self, client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
        response = await client.request(method, url, headers=self.headers, timeout=30, **kwargs)
        if response.status_code == 429 or response.status_code >= 500:
            response.raise_for_status()
        if response.status_code >= 400:
            raise IngestionError("github", f"HTTP {response.status_code}: {response.text[:200]}")
        return response

    async def health_check(self) -> bool:
        if not self.repo:
            return False
        try:
            async with httpx.AsyncClient() as client:
                response = await self._request(client, "GET", f"{self.base_url}/repos/{self.repo}")
            return response.status_code == 200
        except Exception:
            return False

    async def fetch(self, from_ref: str, to_ref: str) -> list[ChangeEvent]:
        if not self.repo:
            raise IngestionError("github", "github_repo is required")
        try:
            async with httpx.AsyncClient() as client:
                if _DATE_RE.match(from_ref):
                    events = await self._fetch_since(client, from_ref, to_ref)
                else:
                    events, since, until = await self._fetch_compare(client, from_ref, to_ref)
                    events.extend(await self._fetch_prs(client, since, until))
            _write_checkpoint(self.output_dir, "github", to_ref)
            return events
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError("github", str(exc)) from exc

    async def _fetch_since(self, client: httpx.AsyncClient, since: str, until: str) -> list[ChangeEvent]:
        """Fetch commits and merged PRs between two ISO datetime strings."""
        events: list[ChangeEvent] = []
        url: str | None = f"{self.base_url}/repos/{self.repo}/commits"
        page = 1
        while url:
            response = await self._request(
                client, "GET", url,
                params={"since": since, "until": until, "per_page": 100, "page": page},
            )
            payload = response.json()
            _write_raw_page(self.output_dir, "github", page, payload)
            for item in payload:
                if self.fetch_diffs:
                    detail_response = await self._request(
                        client, "GET", f"{self.base_url}/repos/{self.repo}/commits/{item.get('sha')}"
                    )
                    item = detail_response.json()
                events.append(_commit_event(item))
            url = _next_link(response.headers.get("Link"))
            page += 1
        events.extend(await self._fetch_prs(client, since, until))
        return events

    async def _fetch_compare(
        self, client: httpx.AsyncClient, from_tag: str, to_tag: str
    ) -> tuple[list[ChangeEvent], str, str]:
        url = f"{self.base_url}/repos/{self.repo}/compare/{from_tag}...{to_tag}"
        page = 1
        events: list[ChangeEvent] = []
        since = ""
        until = ""
        while url:
            response = await self._request(client, "GET", url, params={"per_page": 100, "page": page})
            payload = response.json()
            _write_raw_page(self.output_dir, "github", page, payload)
            if not since:
                since = _date(payload.get("base_commit", {}))
            commits = payload.get("commits", [])
            for item in commits:
                until = _date(item) or until
                detail = item
                if self.fetch_diffs:
                    detail_response = await self._request(
                        client, "GET", f"{self.base_url}/repos/{self.repo}/commits/{item.get('sha')}"
                    )
                    detail = detail_response.json()
                events.append(_commit_event(detail))
            url = _next_link(response.headers.get("Link"))
            page += 1
        if not since and events:
            since = events[0].created_at
        if not until and events:
            until = events[-1].created_at
        return events, since, until

    async def _fetch_prs(self, client: httpx.AsyncClient, since: str, until: str) -> list[ChangeEvent]:
        url = f"{self.base_url}/repos/{self.repo}/pulls"
        page = 1
        events: list[ChangeEvent] = []
        since_dt = _parse(since) if since else None
        until_dt = _parse(until) if until else None
        while url:
            response = await self._request(
                client,
                "GET",
                url,
                params={"state": "closed", "sort": "updated", "direction": "desc", "per_page": 100, "page": page},
            )
            payload = response.json()
            _write_raw_page(self.output_dir, "github_prs", page, payload)
            oldest_updated_at: datetime | None = None
            for pr in payload:
                updated_at = _parse(pr.get("updated_at") or pr.get("merged_at") or "")
                oldest_updated_at = updated_at if oldest_updated_at is None else min(oldest_updated_at, updated_at)
                merged_at = pr.get("merged_at")
                if not merged_at:
                    continue
                merged_dt = _parse(merged_at)
                if since_dt and until_dt and not (since_dt <= merged_dt <= until_dt):
                    continue
                events.append(_pr_event(pr))
            if since_dt and oldest_updated_at and oldest_updated_at < since_dt:
                break
            url = _next_link(response.headers.get("Link"))
            page += 1
        return events


def _parse(value: str) -> datetime:
    if not value:
        return datetime.min
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return parsedate_to_datetime(value)


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


def _next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        section = part.strip().split(";")
        if len(section) < 2:
            continue
        url = section[0].strip()[1:-1]
        rel = section[1].strip()
        if rel == 'rel="next"':
            return url
    return None


def _date(item: dict[str, Any]) -> str:
    commit = item.get("commit", {})
    return (
        commit.get("committer", {}).get("date")
        or commit.get("author", {}).get("date")
        or item.get("commit", {}).get("author", {}).get("date")
        or ""
    )


def _commit_event(item: dict[str, Any]) -> ChangeEvent:
    commit = item.get("commit", {})
    message = commit.get("message", "")
    subject = message.splitlines()[0] if message else item.get("sha", "")
    author = commit.get("author", {}) or {}
    user = item.get("author") or {}
    conventional = None
    match = CONVENTIONAL_RE.match(subject)
    if match:
        conventional = "breaking" if "!" in subject.split(":", 1)[0] else match.group("type")
    if "BREAKING CHANGE:" in message:
        conventional = "breaking"
    return ChangeEvent(
        id=_event_id(),
        source_type="commit",
        source_id=item.get("sha", ""),
        source_system="github",
        title=subject,
        body=message,
        author_name=author.get("name") or user.get("login") or "unknown",
        author_email=author.get("email") or "",
        created_at=_date(item),
        updated_at=_date(item),
        status="done",
        conventional_type=conventional,
        is_merge=bool(re.match(r"^Merge (branch|pull request|remote)", subject)),
        raw_payload=item,
    )


def _pr_event(pr: dict[str, Any]) -> ChangeEvent:
    user = pr.get("user") or {}
    labels = [label.get("name", "") for label in pr.get("labels", []) if isinstance(label, dict)]
    return ChangeEvent(
        id=_event_id(),
        source_type="pr",
        source_id=str(pr.get("number", pr.get("id", ""))),
        source_system="github",
        title=pr.get("title") or "",
        body=pr.get("body") or "",
        author_name=user.get("login") or "unknown",
        author_email="",
        created_at=pr.get("created_at") or "",
        updated_at=pr.get("updated_at") or pr.get("merged_at") or "",
        status="done",
        raw_labels=labels,
        normalized_labels=[label.lower().strip() for label in labels],
        raw_payload=pr,
    )
