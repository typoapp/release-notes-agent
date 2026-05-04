from dataclasses import dataclass, field
from typing import Literal

SourceType = Literal["ticket", "commit", "pr"]
SourceSystem = Literal["jira", "github"]


@dataclass
class ChangeEvent:
    id: str
    source_type: SourceType
    source_id: str
    source_system: SourceSystem
    title: str
    body: str | None
    author_name: str
    author_email: str
    created_at: str
    updated_at: str
    status: str
    raw_labels: list[str] = field(default_factory=list)
    normalized_labels: list[str] = field(default_factory=list)
    priority: str | None = None
    issue_type: str | None = None
    conventional_type: str | None = None
    is_merge: bool = False
    linked_ids: dict = field(default_factory=lambda: {
        "tickets": [], "commits": [], "prs": []
    })
    noise_score: float = 0.0
    raw_payload: dict = field(default_factory=dict)
