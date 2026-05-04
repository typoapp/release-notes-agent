import re

from ..schemas.change_group import ChangeGroup

SIGNAL_WEIGHTS = {
    "ticket_type": 0.90,
    "conventional_commit": 0.80,
    "label": 0.70,
    "title_keyword": 0.50,
}

TICKET_TYPE_MAP = {
    "bug": "bug_fixes",
    "defect": "bug_fixes",
    "hotfix": "bug_fixes",
    "story": "features",
    "feature": "features",
    "epic": "features",
    "task": "improvements",
    "improvement": "improvements",
    "chore": "improvements",
}

CONVENTIONAL_MAP = {
    "feat": "features",
    "fix": "bug_fixes",
    "perf": "improvements",
    "refactor": "improvements",
    "breaking": "breaking_changes",
}

FEATURE_KEYWORDS = ["add", "introduce", "implement", "new", "create", "launch", "release"]
BUG_KEYWORDS = ["fix", "resolve", "patch", "correct", "repair", "address", "handle"]


def classify(groups: list[ChangeGroup]) -> list[ChangeGroup]:
    for group in groups:
        votes = {"features": 0.0, "improvements": 0.0, "bug_fixes": 0.0, "breaking_changes": 0.0}
        ticket = group.source_ticket
        if ticket and ticket.issue_type in TICKET_TYPE_MAP:
            votes[TICKET_TYPE_MAP[ticket.issue_type]] += SIGNAL_WEIGHTS["ticket_type"]
        for commit in group.source_commits:
            category = CONVENTIONAL_MAP.get(commit.conventional_type or "")
            if category:
                votes[category] += SIGNAL_WEIGHTS["conventional_commit"]
        for label in group.canonical_labels:
            if label in {"bug", "defect"}:
                votes["bug_fixes"] += SIGNAL_WEIGHTS["label"]
            elif label in {"feat", "feature"}:
                votes["features"] += SIGNAL_WEIGHTS["label"]
            elif label in {"improvement", "perf", "performance", "chore"}:
                votes["improvements"] += SIGNAL_WEIGHTS["label"]
            elif label in {"breaking", "breaking-change", "semver:major", "migration-required"}:
                votes["breaking_changes"] += SIGNAL_WEIGHTS["label"]
        lowered = group.canonical_title.lower()
        if any(word in lowered for word in FEATURE_KEYWORDS):
            votes["features"] += SIGNAL_WEIGHTS["title_keyword"]
        if any(word in lowered for word in BUG_KEYWORDS):
            votes["bug_fixes"] += SIGNAL_WEIGHTS["title_keyword"]

        breaking_signals = _breaking_signals(group)
        if breaking_signals:
            votes["breaking_changes"] += 1.0
            group.is_breaking = True
            group.breaking_signals = breaking_signals

        classification, score = max(votes.items(), key=lambda item: item[1])
        if score < 0.5 and not ticket:
            classification = "improvements"
            score = 0.5
        group.classification = classification
        group.classification_confidence = score
    return groups


def _breaking_signals(group: ChangeGroup) -> list[str]:
    signals: list[str] = []
    for commit in group.source_commits:
        body = commit.body or ""
        title = commit.title or ""
        if commit.conventional_type == "breaking":
            signals.append(f"conventional commit marked breaking: {commit.source_id}")
        if "BREAKING CHANGE:" in body or "BREAKING CHANGE:" in title:
            signals.append(f"BREAKING CHANGE footer: {commit.source_id}")
        diff = _diff_text(commit.raw_payload)
        if re.search(r"\bDROP COLUMN\b|dropColumn\(", diff, re.IGNORECASE):
            signals.append(f"database column drop: {commit.source_id}")
        if re.search(r"\bRENAME COLUMN\b|renameColumn\(", diff, re.IGNORECASE):
            signals.append(f"database column rename: {commit.source_id}")
        if re.search(r"^-.*@(GetMapping|PostMapping)\(", diff, re.MULTILINE):
            signals.append(f"REST endpoint removed: {commit.source_id}")
    for pr in group.source_prs:
        if set(pr.normalized_labels) & {"breaking-change", "semver:major", "breaking"}:
            signals.append(f"PR label marked breaking: {pr.source_id}")
    if group.source_ticket and set(group.source_ticket.normalized_labels) & {"breaking", "migration-required", "breaking-change"}:
        signals.append(f"ticket label marked breaking: {group.source_ticket.source_id}")
    return list(dict.fromkeys(signals))


def _diff_text(payload: dict) -> str:
    parts: list[str] = []
    for file in payload.get("files", []) if isinstance(payload, dict) else []:
        parts.append(file.get("patch", ""))
    return "\n".join(parts)
