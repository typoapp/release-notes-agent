import hashlib
import re
from datetime import datetime

import numpy as np
import tiktoken

from ..schemas.change_event import ChangeEvent

JIRA_RE = re.compile(r"\b([A-Z]{2,10}-\d+)\b")
GH_RE = re.compile(r"(?:closes?|fixes?|resolves?)\s+#(\d+)", re.IGNORECASE)
PR_RE = re.compile(r"(?:\(#(\d+)\)|pull request #(\d+)|pr #(\d+))", re.IGNORECASE)
BRANCH_RE = re.compile(r"(?:[a-z]+/)?([A-Z]{2,10}-\d+|\d+)(?:[-_/].*)?$", re.IGNORECASE)


class Correlator:
    def __init__(self, use_semantic_linking: bool = False):
        self.use_semantic_linking = use_semantic_linking

    def correlate(self, events: list[ChangeEvent]) -> list[ChangeEvent]:
        tickets = [event for event in events if event.source_type == "ticket"]
        prs = [event for event in events if event.source_type == "pr"]
        candidates = [event for event in events if event.source_type in {"commit", "pr"}]
        ticket_by_key = {ticket.source_id.upper(): ticket for ticket in tickets}
        ticket_by_number = {ticket.source_id: ticket for ticket in tickets}
        pr_by_number = {pr.source_id: pr for pr in prs}

        for event in candidates:
            high_confidence = False
            if event.source_type == "commit":
                for pr_number in _extract_pr_numbers(f"{event.title}\n{event.body or ''}"):
                    pr = pr_by_number.get(pr_number)
                    if pr:
                        _add_edge(event, "prs", pr.id, 0.90, "pr_number")
                        _add_edge(pr, "commits", event.id, 0.90, "pr_number")

            explicit_ids = _extract_ticket_ids(event.body or "")
            for ticket_id in explicit_ids:
                if self._link(event, ticket_by_key, ticket_by_number, ticket_id, 0.95, "explicit_body"):
                    high_confidence = True

            branch = (event.raw_payload.get("head") or {}).get("ref", "") if event.source_type == "pr" else ""
            for ticket_id in _extract_branch_ids(branch):
                if self._link(event, ticket_by_key, ticket_by_number, ticket_id, 0.85, "branch"):
                    high_confidence = True

            text = f"{event.title}\n{(event.body or '')[:500]}"
            for ticket_id in _extract_ticket_ids(text):
                if self._link(event, ticket_by_key, ticket_by_number, ticket_id, 0.80, "pr_text"):
                    high_confidence = True

            if not high_confidence:
                ticket = _best_author_time_ticket(event, tickets)
                if ticket:
                    _add_edge(event, "tickets", ticket.id, 0.40, "author_time")
                    _add_edge(ticket, _reverse_key(event), event.id, 0.40, "author_time")

        if self.use_semantic_linking:
            self._semantic_link(candidates, tickets)
        return events

    def _link(
        self,
        event: ChangeEvent,
        ticket_by_key: dict[str, ChangeEvent],
        ticket_by_number: dict[str, ChangeEvent],
        ticket_id: str,
        confidence: float,
        strategy: str,
    ) -> bool:
        ticket = ticket_by_key.get(ticket_id.upper()) or ticket_by_number.get(ticket_id)
        if not ticket:
            return False
        _add_edge(event, "tickets", ticket.id, confidence, strategy)
        _add_edge(ticket, _reverse_key(event), event.id, confidence, strategy)
        return True

    def _semantic_link(self, candidates: list[ChangeEvent], tickets: list[ChangeEvent]) -> None:
        ticket_vectors = [(ticket, _cheap_embedding(ticket.title)) for ticket in tickets]
        for event in candidates:
            if event.linked_ids.get("tickets") or len(event.title) <= 15:
                continue
            vector = _cheap_embedding(event.title)
            best_ticket = None
            best_score = 0.0
            for ticket, ticket_vector in ticket_vectors:
                score = _cosine(vector, ticket_vector)
                if score > best_score:
                    best_ticket = ticket
                    best_score = score
            if best_ticket and best_score >= 0.82:
                _add_edge(event, "tickets", best_ticket.id, float(best_score), "semantic")
                _add_edge(best_ticket, _reverse_key(event), event.id, float(best_score), "semantic")


def correlate(events: list[ChangeEvent], use_semantic_linking: bool = False) -> list[ChangeEvent]:
    return Correlator(use_semantic_linking=use_semantic_linking).correlate(events)


def _extract_ticket_ids(text: str) -> list[str]:
    ids = JIRA_RE.findall(text or "")
    ids.extend(GH_RE.findall(text or ""))
    return list(dict.fromkeys(ids))


def _extract_pr_numbers(text: str) -> list[str]:
    ids: list[str] = []
    for match in PR_RE.findall(text or ""):
        ids.extend(value for value in match if value)
    return list(dict.fromkeys(ids))


def _extract_branch_ids(branch: str) -> list[str]:
    if not branch:
        return []
    jira_ids = JIRA_RE.findall(branch)
    match = BRANCH_RE.match(branch)
    if match:
        jira_ids.append(match.group(1))
    return list(dict.fromkeys(jira_ids))


def _add_edge(event: ChangeEvent, bucket: str, target_id: str, confidence: float, strategy: str) -> None:
    event.linked_ids.setdefault(bucket, [])
    if any(edge.get("id") == target_id for edge in event.linked_ids[bucket] if isinstance(edge, dict)):
        return
    event.linked_ids[bucket].append({"id": target_id, "confidence": confidence, "strategy": strategy})


def _reverse_key(event: ChangeEvent) -> str:
    return "commits" if event.source_type == "commit" else "prs"


def _same_author(left: ChangeEvent, right: ChangeEvent) -> bool:
    assignee = _ticket_assignee_email(right) or right.author_email
    return bool(left.author_email and assignee and left.author_email == assignee)


def _best_author_time_ticket(event: ChangeEvent, tickets: list[ChangeEvent]) -> ChangeEvent | None:
    matches = [
        ticket
        for ticket in tickets
        if _same_author(event, ticket)
        and _ticket_assignee_email(ticket)
        and _within(event.created_at, ticket.created_at, ticket.updated_at)
    ]
    if not matches:
        return None
    event_time = _parse(event.created_at)
    return min(matches, key=lambda ticket: abs((_parse(ticket.updated_at) - event_time).total_seconds()))


def _ticket_assignee_email(ticket: ChangeEvent) -> str:
    fields = ticket.raw_payload.get("fields", {}) if isinstance(ticket.raw_payload, dict) else {}
    assignee = fields.get("assignee") or {}
    return (ticket.raw_payload.get("assignee_email") or assignee.get("emailAddress") or "").lower()


def _within(value: str, start: str, end: str) -> bool:
    try:
        current = _parse(value)
        return _parse(start) <= current <= _parse(end)
    except ValueError:
        return False


def _parse(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _cheap_embedding(text: str) -> np.ndarray:
    try:
        tokens = tiktoken.get_encoding("cl100k_base").encode(text.lower())
    except Exception:
        tokens = [int(hashlib.sha1(part.encode()).hexdigest(), 16) % 10_000 for part in text.lower().split()]
    vector = np.zeros(64, dtype=float)
    for token in tokens:
        vector[token % 64] += 1.0
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    denom = np.linalg.norm(left) * np.linalg.norm(right)
    return float(np.dot(left, right) / denom) if denom else 0.0
