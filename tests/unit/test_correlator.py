from releasenotes.pipeline.correlator import correlate
from releasenotes.schemas.change_event import ChangeEvent


def make_event(source_type, source_id, title, body="", email="ana@example.com"):
    return ChangeEvent(
        id=f"ce_{source_id}",
        source_type=source_type,
        source_id=source_id,
        source_system="jira" if source_type == "ticket" else "github",
        title=title,
        body=body,
        author_name="Ana",
        author_email=email,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-02T00:00:00+00:00",
        status="done",
    )


def edge_ids(event):
    return [edge["id"] for edge in event.linked_ids["tickets"]]


def test_explicit_jira_id_in_commit_body_links_with_confidence():
    ticket = make_event("ticket", "PROJ-123", "Fix login")
    commit = make_event("commit", "abc", "fix: login", "Fixes PROJ-123")
    correlate([ticket, commit])
    assert ticket.id in edge_ids(commit)
    assert commit.linked_ids["tickets"][0]["confidence"] == 0.95


def test_no_ticket_id_keeps_ticket_links_empty():
    ticket = make_event("ticket", "PROJ-123", "Fix login")
    commit = make_event("commit", "abc", "fix: login", "No issue reference")
    correlate([ticket, commit])
    assert commit.linked_ids["tickets"] == []


def test_multiple_tickets_in_one_commit_link_both():
    ticket_a = make_event("ticket", "PROJ-123", "Fix login")
    ticket_b = make_event("ticket", "PROJ-124", "Fix billing")
    commit = make_event("commit", "abc", "fix: login", "Fixes PROJ-123 and PROJ-124")
    correlate([ticket_a, ticket_b, commit])
    assert set(edge_ids(commit)) == {ticket_a.id, ticket_b.id}


def test_commit_title_with_pr_number_links_to_pr():
    pr = make_event("pr", "15368", "Update sponsors")
    commit = make_event("commit", "abc", "Update sponsors (#15368)", "")
    correlate([pr, commit])
    assert commit.linked_ids["prs"][0]["id"] == pr.id
    assert commit.linked_ids["prs"][0]["confidence"] == 0.90
