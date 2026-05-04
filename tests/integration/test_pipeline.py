import json
import re

import pytest

from releasenotes.config import Settings
from releasenotes.ingestion.github import GitHubIngestor
from releasenotes.ingestion.jira import JIRAIngestor
from releasenotes.llm.base import BaseLLMProvider, LLMResponse
from releasenotes.output.base import BaseFormatter
from releasenotes.pipeline.generator import ReleaseNotePipeline


class FakeProvider(BaseLLMProvider):
    model = "fake-model"

    async def complete(self, request):
        payload = json.loads(re.search(r"Changes \(JSON\):\n(.*?)\n\nRules reminder:", request.user, re.S).group(1))
        bullets = []
        for item in payload:
            prefix = "⚠️ BREAKING: " if item["is_breaking"] else ""
            bullets.append(f"- {prefix}{item['title']} ({item['id']})")
        return LLMResponse("\n".join(bullets), 1, 1, self.model, "stop")

    async def stream(self, request):
        yield ""

    def count_tokens(self, text):
        return len(text.split())

    @property
    def context_window(self):
        return 16_000

    @property
    def name(self):
        return "fake"


class MemoryFormatter(BaseFormatter):
    def __init__(self):
        self.notes = None

    async def write(self, notes):
        self.notes = notes
        return "memory"


@pytest.mark.asyncio
async def test_full_pipeline_with_mocked_http(httpx_mock, tmp_path):
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"https://api.github.com/repos/acme/widget/compare/v1\.2\.0\.\.\.v1\.3\.0.*"),
        json=_github_compare(),
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"https://api.github.com/repos/acme/widget/pulls.*"),
        json=_github_prs(),
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"https://jira\.example\.com/rest/api/3/search.*"),
        json=_jira_search(),
    )
    settings = Settings()
    settings.output.output_dir = str(tmp_path)
    formatter = MemoryFormatter()
    pipeline = ReleaseNotePipeline(
        settings,
        llm_provider=FakeProvider(),
        ingestors=[
            JIRAIngestor(url="https://jira.example.com", token="token", project="PROJ", output_dir=str(tmp_path)),
            GitHubIngestor(token="token", repo="acme/widget", output_dir=str(tmp_path)),
        ],
        formatters=[formatter],
    )

    notes = await pipeline.generate("v1.2.0", "v1.3.0")

    assert len(notes.features) == 2
    assert len(notes.improvements) == 1
    assert len(notes.bug_fixes) == 2
    assert notes.coverage_pct > 0
    assert formatter.notes is notes


def _jira_search():
    types = ["Bug", "Bug", "Story", "Story", "Task"]
    return {
        "total": 5,
        "issues": [
            {
                "key": f"PROJ-{index}",
                "fields": {
                    "summary": f"{kind} change {index}",
                    "description": {"content": [{"content": [{"text": f"This change updates service behavior for ticket {index}."}]}]},
                    "issuetype": {"name": kind},
                    "status": {"name": "Done"},
                    "labels": [],
                    "priority": {"name": "Medium"},
                    "assignee": {"displayName": "Ana", "emailAddress": "ana@example.com"},
                    "created": "2026-01-01T00:00:00+00:00",
                    "updated": "2026-01-10T00:00:00+00:00",
                },
            }
            for index, kind in enumerate(types, start=1)
        ],
    }


def _github_compare():
    linked_messages = [
        "fix: repair login\n\nFixes PROJ-1",
        "fix: repair billing\n\nFixes PROJ-2",
        "feat: add export\n\nImplements PROJ-3",
        "feat: add audit log\n\nImplements PROJ-4",
        "chore: tune worker\n\nRefs PROJ-5",
    ]
    noisy = [
        "update docs",
        "bump deps",
        "minor fix",
        "update readme",
        "wip: scratch",
    ]
    commits = []
    for index, message in enumerate(linked_messages + noisy, start=1):
        commits.append(
            {
                "sha": f"sha{index}",
                "commit": {
                    "message": message,
                    "author": {"name": "Ana", "email": "ana@example.com", "date": f"2026-01-{index:02d}T00:00:00Z"},
                    "committer": {"date": f"2026-01-{index:02d}T00:00:00Z"},
                },
            }
        )
    return {
        "base_commit": {"commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}},
        "commits": commits,
    }


def _github_prs():
    return [
        {
            "number": index,
            "title": f"PROJ-{index} pull request",
            "body": f"Completes PROJ-{index}",
            "user": {"login": "ana"},
            "labels": [],
            "head": {"ref": f"feature/PROJ-{index}-work"},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-05T00:00:00Z",
            "merged_at": "2026-01-05T00:00:00Z",
        }
        for index in range(1, 4)
    ]
