import re
from collections import deque
from uuid import uuid4

from ..schemas.change_event import ChangeEvent
from ..schemas.change_group import ChangeGroup

NOISE_PATTERNS = [
    r"^(wip|WIP)[\s:]",
    r"^(fixup|squash)!",
    r"^Merge (branch|pull request)",
    r"^(minor|tiny|small)\s+(fix|change|tweak)",
    r"^bump (version|deps|dependencies)",
    r"^update (changelog|readme|docs|lock)",
    r"^(revert|undo)\s",
]


def deduplicate(events: list[ChangeEvent]) -> list[ChangeGroup]:
    events_by_id = {event.id: event for event in events}
    graph = _build_graph(events)
    seen: set[str] = set()
    groups: list[ChangeGroup] = []
    for event in events:
        if event.id in seen:
            continue
        component = _component(event.id, graph, seen)
        members = [events_by_id[node] for node in component if node in events_by_id]
        groups.append(_group(members))
    return groups


def _build_graph(events: list[ChangeEvent]) -> dict[str, set[str]]:
    graph = {event.id: set() for event in events}
    valid = set(graph)
    for event in events:
        for links in event.linked_ids.values():
            for edge in links:
                target = edge.get("id") if isinstance(edge, dict) else edge
                if target in valid:
                    graph[event.id].add(target)
                    graph[target].add(event.id)
    return graph


def _component(start: str, graph: dict[str, set[str]], seen: set[str]) -> set[str]:
    queue: deque[str] = deque([start])
    component: set[str] = set()
    seen.add(start)
    while queue:
        node = queue.popleft()
        component.add(node)
        for target in graph.get(node, set()):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return component


def _group(members: list[ChangeEvent]) -> ChangeGroup:
    tickets = [event for event in members if event.source_type == "ticket"]
    prs = [event for event in members if event.source_type == "pr"]
    commits = [event for event in members if event.source_type == "commit"]
    for commit in commits:
        if any(re.search(pattern, commit.title or "") for pattern in NOISE_PATTERNS):
            commit.noise_score = 0.9
    source_ticket = tickets[0] if tickets else None
    title = (
        source_ticket.title
        if source_ticket
        else prs[0].title
        if prs
        else next((commit.title for commit in commits if commit.noise_score < 0.9), commits[0].title if commits else "")
    )
    body = source_ticket.body if source_ticket else (prs[0].body if prs else None)
    all_labels = []
    for event in members:
        all_labels.extend(event.normalized_labels)
    all_commits_noisy = bool(commits) and all(commit.noise_score >= 0.9 for commit in commits)
    noise_score = 0.9 if all_commits_noisy and not source_ticket else 0.0
    return ChangeGroup(
        id=f"cg_{uuid4().hex[:8]}",
        canonical_title=title,
        canonical_body=body,
        canonical_labels=sorted(set(all_labels)),
        source_ticket=source_ticket,
        source_commits=commits,
        source_prs=prs,
        classification=None,
        classification_confidence=0.0,
        is_breaking=False,
        breaking_signals=[],
        authors=sorted({event.author_name for event in members if event.author_name}),
        noise_score=noise_score,
        key_facts=_facts("\n".join(filter(None, [source_ticket.body if source_ticket else "", *(pr.body or "" for pr in prs)]))),
    )


def _facts(text: str) -> list[str]:
    cleaned = re.sub(r"[*_`>#-]", "", text or "")
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    facts: list[str] = []
    for sentence in sentences:
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if len(sentence.split()) <= 8:
            continue
        if not re.search(r"\b(is|are|was|were|be|being|been|add|adds|added|fix|fixes|fixed|allow|allows|allowed|enable|enables|enabled|remove|removes|removed|update|updates|updated)\b", sentence, re.IGNORECASE):
            continue
        facts.append(sentence[:120])
        if len(facts) == 5:
            break
    return facts
