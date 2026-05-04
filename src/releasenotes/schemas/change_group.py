from dataclasses import dataclass, field
from typing import Literal

from .change_event import ChangeEvent

Classification = Literal["features", "improvements", "bug_fixes", "breaking_changes"]


@dataclass
class ChangeGroup:
    id: str
    canonical_title: str
    canonical_body: str | None
    canonical_labels: list[str]
    source_ticket: ChangeEvent | None
    source_commits: list[ChangeEvent]
    source_prs: list[ChangeEvent]
    classification: Classification | None
    classification_confidence: float
    is_breaking: bool
    breaking_signals: list[str]
    authors: list[str]
    noise_score: float
    key_facts: list[str] = field(default_factory=list)
    group_id: str | None = None
