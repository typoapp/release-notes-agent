from releasenotes.pipeline.normalizer import normalize
from releasenotes.schemas.change_event import ChangeEvent


def commit(title, body="", email="Ana+github@Example.com", labels=None):
    return ChangeEvent(
        id="ce_1",
        source_type="commit",
        source_id="abc",
        source_system="github",
        title=title,
        body=body,
        author_name="Ana",
        author_email=email,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        status="done",
        raw_labels=labels or [],
    )


def test_normalizes_email_labels_and_conventional_type():
    result = normalize([commit("feat!: add export", labels=["BugFix", " enhancement "])])[0]
    assert result.author_email == "ana@example.com"
    assert result.normalized_labels == ["bug", "feat"]
    assert result.conventional_type == "breaking"


def test_skips_merge_commits():
    assert normalize([commit("Merge branch main")]) == []
