from releasenotes.pipeline.classifier import classify
from releasenotes.schemas.change_event import ChangeEvent
from releasenotes.schemas.change_group import ChangeGroup


def event(**kwargs):
    data = {
        "id": "ce_1",
        "source_type": "ticket",
        "source_id": "PROJ-1",
        "source_system": "jira",
        "title": "Fix broken login",
        "body": "",
        "author_name": "Ana",
        "author_email": "ana@example.com",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-02T00:00:00+00:00",
        "status": "done",
    }
    data.update(kwargs)
    return ChangeEvent(**data)


def group(ticket=None, commits=None):
    return ChangeGroup(
        id="cg_12345678",
        canonical_title=ticket.title if ticket else "Billing export cleanup",
        canonical_body=None,
        canonical_labels=[],
        source_ticket=ticket,
        source_commits=commits or [],
        source_prs=[],
        classification=None,
        classification_confidence=0.0,
        is_breaking=False,
        breaking_signals=[],
        authors=["Ana"],
        noise_score=0.0,
    )


def test_ticket_type_bug_maps_to_bug_fixes():
    classified = classify([group(ticket=event(issue_type="bug"))])[0]
    assert classified.classification == "bug_fixes"


def test_conventional_commit_feat_maps_to_features():
    commit = event(source_type="commit", source_id="abc", title="feat: add reports", conventional_type="feat")
    classified = classify([group(commits=[commit])])[0]
    assert classified.classification == "features"


def test_breaking_change_footer_sets_breaking():
    commit = event(source_type="commit", source_id="abc", title="refactor api", body="BREAKING CHANGE: remove v1")
    classified = classify([group(commits=[commit])])[0]
    assert classified.is_breaking is True


def test_conflicting_signals_ticket_type_wins():
    ticket = event(issue_type="bug")
    commit = event(source_type="commit", source_id="abc", title="feat: add reports", conventional_type="feat")
    classified = classify([group(ticket=ticket, commits=[commit])])[0]
    assert classified.classification == "bug_fixes"


def test_missing_ticket_and_conventional_defaults_to_improvements():
    classified = classify([group()])[0]
    assert classified.classification == "improvements"
