import re
from typing import Any
from uuid import uuid4

from ..schemas.change_event import ChangeEvent

LABEL_MAP = {
    "bugfix": "bug",
    "bug-fix": "bug",
    "defect": "bug",
    "feature": "feat",
    "new-feature": "feat",
    "enhancement": "feat",
    "improvement": "improvement",
    "perf": "improvement",
    "performance": "improvement",
}
CONVENTIONAL_RE = re.compile(
    r"^(?P<type>feat|fix|chore|refactor|perf|docs|test|breaking)(\(.+\))?(!)?:\s"
)
MERGE_RE = re.compile(r"^Merge (branch|pull request|remote)")


def normalize(events: list[ChangeEvent | dict[str, Any]]) -> list[ChangeEvent]:
    normalized: list[ChangeEvent] = []
    for event in events:
        change = event if isinstance(event, ChangeEvent) else _from_raw(event)
        change.author_email = _normalize_email(change.author_email)
        change.normalized_labels = [_normalize_label(label) for label in change.raw_labels]
        change.normalized_labels = [label for label in change.normalized_labels if label]
        subject = change.title or ""
        body = change.body or ""
        match = CONVENTIONAL_RE.match(subject)
        if match:
            prefix = subject.split(":", 1)[0]
            change.conventional_type = "breaking" if "!" in prefix else match.group("type")
        if "BREAKING CHANGE:" in body:
            change.conventional_type = "breaking"
        change.is_merge = bool(change.is_merge or MERGE_RE.match(subject))
        if change.source_type == "commit" and change.is_merge:
            continue
        normalized.append(change)
    return normalized


def _normalize_email(email: str) -> str:
    local, sep, domain = (email or "").strip().lower().partition("@")
    local = re.sub(r"\+github$", "", local)
    return f"{local}{sep}{domain}" if sep else local


def _normalize_label(label: str) -> str:
    cleaned = label.lower().strip()
    return LABEL_MAP.get(cleaned, cleaned)


def _from_raw(raw: dict[str, Any]) -> ChangeEvent:
    return ChangeEvent(
        id=raw.get("id") or f"ce_{uuid4().hex[:8]}",
        source_type=raw.get("source_type", "commit"),
        source_id=str(raw.get("source_id", raw.get("sha", ""))),
        source_system=raw.get("source_system", "github"),
        title=raw.get("title") or raw.get("message", "").splitlines()[0],
        body=raw.get("body") or raw.get("message"),
        author_name=raw.get("author_name", "unknown"),
        author_email=raw.get("author_email", ""),
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", raw.get("created_at", "")),
        status=raw.get("status", "done"),
        raw_labels=list(raw.get("raw_labels", raw.get("labels", []))),
        priority=raw.get("priority"),
        issue_type=raw.get("issue_type"),
        conventional_type=raw.get("conventional_type"),
        is_merge=bool(raw.get("is_merge", False)),
        raw_payload=raw,
    )
