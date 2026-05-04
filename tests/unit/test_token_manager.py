from releasenotes.llm.base import BaseLLMProvider
from releasenotes.pipeline.token_manager import plan_calls
from releasenotes.schemas.change_group import ChangeGroup


class Provider(BaseLLMProvider):
    def __init__(self, window):
        self._window = window

    async def complete(self, request):
        raise NotImplementedError

    async def stream(self, request):
        yield ""

    def count_tokens(self, text):
        return len(text.split())

    @property
    def context_window(self):
        return self._window

    @property
    def name(self):
        return "test"


def make_groups(count):
    return [
        ChangeGroup(
            id=f"cg_{i:08x}",
            canonical_title=f"Change {i}",
            canonical_body=None,
            canonical_labels=[],
            source_ticket=None,
            source_commits=[],
            source_prs=[],
            classification="features",
            classification_confidence=1.0,
            is_breaking=False,
            breaking_signals=[],
            authors=[],
            noise_score=0.0,
        )
        for i in range(count)
    ]


def test_ten_groups_single_call_planned():
    chunks = plan_calls(make_groups(10), Provider(16_000))
    assert len(chunks) == 1


def test_five_hundred_groups_multiple_chunks_max_twenty():
    chunks = plan_calls(make_groups(500), Provider(16_000))
    assert len(chunks) > 1
    assert all(len(chunk) <= 20 for chunk in chunks)


def test_chunk_size_respects_context_window():
    chunks = plan_calls(make_groups(10), Provider(7_000), output_budget=4_096)
    assert all(len(chunk) <= 3 for chunk in chunks)
