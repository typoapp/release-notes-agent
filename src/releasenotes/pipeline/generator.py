import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from uuid import uuid4

from ..config import Settings
from ..ingestion.base import BaseIngestor
from ..ingestion.registry import get_ingestor
from ..llm.base import BaseLLMProvider, LLMRequest
from ..llm.registry import get_provider
from ..output.base import BaseFormatter
from ..output.registry import get_formatter
from ..schemas.change_event import ChangeEvent
from ..schemas.change_group import ChangeGroup
from ..schemas.release_notes import ReleaseNotes
from .classifier import classify
from .correlator import correlate
from .deduplicator import deduplicate
from .normalizer import normalize
from .token_manager import OUTPUT_TOKENS_PER_GROUP_ESTIMATE, plan_calls

SYSTEM_PROMPT = """You are a technical writer generating structured release notes for software engineers.

STRICT RULES — violating any rule causes a retry:
1. Only describe changes explicitly listed in the provided JSON.
2. Never invent capabilities, behaviors, or improvements not in the input data.
3. Every bullet MUST end with the change group ID in parentheses, e.g. (cg_a3f9b).
4. One bullet per change group. Never combine two groups into one bullet.
5. If is_breaking is true, start the bullet with: ⚠️ BREAKING:
6. Write for a technical audience. Use specific names (endpoint paths, flag names,
   field names) when present in key_facts.
7. Write each bullet as a clear, complete sentence that a PM or developer can quickly
   understand. Convert terse PR or commit titles into natural release-note prose.
8. Keep bullets concise: usually 18-35 words. Mention impact or user-visible outcome
   only when it is present in the JSON.
9. Do not add introductory prose, section headers, or closing remarks.
10. Output only a markdown list. Nothing else.
"""


class ReleaseNotePipeline:
    def __init__(
        self,
        config: Settings,
        llm_provider: BaseLLMProvider | None = None,
        ingestors: list[BaseIngestor] | None = None,
        formatters: list[BaseFormatter] | None = None,
        run_id: str | None = None,
    ):
        self.config = config
        self.run_id = run_id or f"rn_{uuid4().hex[:10]}"
        self.logger = get_logger(__name__, self.run_id)
        self.llm_provider = llm_provider
        self._ingestors = ingestors
        self._formatters = formatters
        # Populated after generate() — available for inspection by callers
        self.fetched_events: list[ChangeEvent] = []
        self.change_groups: list[ChangeGroup] = []

    async def generate(self, from_tag: str, to_tag: str, dry_run: bool = False) -> ReleaseNotes:
        started = time.perf_counter()
        self._log("info", {"stage": "pipeline", "inputs": {"from_tag": from_tag, "to_tag": to_tag, "dry_run": dry_run}})
        try:
            events = await self._ingest(from_tag, to_tag)
            self.fetched_events = events
            normalized = self._stage("normalizer", lambda: normalize(events), {"events": len(events)})
            linked = self._stage(
                "correlator",
                lambda: correlate(normalized, self.config.ingestion.use_semantic_linking),
                {"events": len(normalized)},
            )
            groups = self._stage("deduplicator", lambda: deduplicate(linked), {"events": len(linked)})
            groups = self._stage("classifier", lambda: classify(groups), {"groups": len(groups)})
            self.change_groups = groups
            llm_groups = [group for group in groups if group.noise_score < 0.9]
            notes = await self._render_notes(from_tag, to_tag, llm_groups, normalized, dry_run)
            await self._write_outputs(notes)
            self._log(
                "info",
                {
                    "stage": "pipeline",
                    "outputs": {"summary": notes.summary()},
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            return notes
        except Exception:
            self.logger.exception(json.dumps({"stage": "pipeline", "run_id": self.run_id}))
            raise

    async def _ingest(self, from_tag: str, to_tag: str) -> list[ChangeEvent]:
        ingestors = self._ingestors if self._ingestors is not None else self._build_ingestors()
        started = time.perf_counter()
        self._log("info", {"stage": "ingestion", "inputs": {"sources": [type(i).__name__ for i in ingestors]}})
        results = await asyncio.gather(*(ingestor.fetch(from_tag, to_tag) for ingestor in ingestors), return_exceptions=True)
        events: list[ChangeEvent] = []
        warnings: list[str] = []
        for result in results:
            if isinstance(result, Exception):
                warnings.append(str(result))
                self.logger.exception(json.dumps({"stage": "ingestion", "warnings": [str(result)], "run_id": self.run_id}))
                continue
            events.extend(result)
        if len(warnings) == len(ingestors):
            raise RuntimeError(f"All ingestors failed: {'; '.join(warnings)}")
        self._log(
            "info",
            {
                "stage": "ingestion",
                "outputs": {"events": len(events)},
                "warnings": warnings,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return events

    def _stage(self, name: str, func, inputs: dict) -> object:
        started = time.perf_counter()
        self._log("info", {"stage": name, "inputs": inputs})
        result = func()
        size = len(result) if hasattr(result, "__len__") else 0
        self._log("info", {"stage": name, "outputs": {"count": size}, "duration_ms": int((time.perf_counter() - started) * 1000)})
        return result

    async def _render_notes(
        self,
        from_tag: str,
        to_tag: str,
        groups: list[ChangeGroup],
        events: list[ChangeEvent],
        dry_run: bool,
    ) -> ReleaseNotes:
        buckets = {
            "features": [group for group in groups if group.classification == "features"],
            "improvements": [group for group in groups if group.classification == "improvements"],
            "bug_fixes": [group for group in groups if group.classification == "bug_fixes"],
            "breaking_changes": [group for group in groups if group.classification == "breaking_changes" or group.is_breaking],
        }
        if dry_run:
            rendered = {key: [_dry_bullet(group) for group in value] for key, value in buckets.items()}
            provider_name = self.config.llm.provider
            model = self.config.llm.model
        else:
            provider = self.llm_provider or get_provider(
                self.config.llm.provider,
                api_key=self.config.llm.api_key,
                model=self.config.llm.model,
            )
            rendered = {
                key: await self._generate_bucket(key, value, provider, to_tag)
                for key, value in buckets.items()
            }
            provider_name = provider.name
            model = getattr(provider, "model", self.config.llm.model)
        tickets_total = len([event for event in events if event.source_type == "ticket"])
        tickets_included = len({group.source_ticket.id for group in groups if group.source_ticket})
        untracked = len([event for event in events if event.source_type == "commit" and not event.linked_ids.get("tickets")])
        return ReleaseNotes(
            version=to_tag,
            from_tag=from_tag,
            to_tag=to_tag,
            generated_at=datetime.now(timezone.utc).isoformat(),
            features=rendered["features"],
            improvements=rendered["improvements"],
            bug_fixes=rendered["bug_fixes"],
            breaking_changes=rendered["breaking_changes"],
            untracked_commits_count=untracked,
            coverage_pct=(tickets_included / tickets_total) if tickets_total else 1.0,
            run_id=self.run_id,
            llm_provider=provider_name,
            llm_model=model,
        )

    async def _generate_bucket(
        self,
        classification: str,
        groups: list[ChangeGroup],
        provider: BaseLLMProvider,
        version: str,
    ) -> list[str]:
        if not groups:
            return []
        chunks = plan_calls(groups, provider, self.config.llm.max_output_tokens)
        partials = await asyncio.gather(
            *(
                self._call_llm(_bucket_prompt(classification, version, chunk), chunk, provider)
                for chunk in chunks
            )
        )
        if len(partials) == 1:
            return _bullets(partials[0])
        if len(groups) * OUTPUT_TOKENS_PER_GROUP_ESTIMATE > self.config.llm.max_output_tokens:
            self._log(
                "warning",
                {
                    "stage": "llm_reduce",
                    "warnings": ["Skipping reduce because the section is too large for one output budget."],
                    "groups": len(groups),
                },
            )
            return [bullet for partial in partials for bullet in _bullets(partial)]
        reduce_prompt = (
            f"Merge, deduplicate, and unify tone for the {classification} release note bullets below. "
            "Keep every valid cg_* ID exactly once, preserve the existing IDs, and output only a markdown list. "
            "Use polished, complete sentences suitable for PMs and developers.\n\n"
            + "\n".join(partials)
        )
        reduced = await self._call_llm(reduce_prompt, groups, provider)
        return _bullets(reduced)

    async def _call_llm(self, user_prompt: str, groups: list[ChangeGroup], provider: BaseLLMProvider) -> str:
        prompt = user_prompt
        for attempt in range(3):
            try:
                response = await provider.complete(
                    LLMRequest(
                        system=SYSTEM_PROMPT,
                        user=prompt,
                        max_tokens=self.config.llm.max_output_tokens,
                        temperature=self.config.llm.temperature,
                    )
                )
            except Exception as exc:
                self._log("warning", {"stage": "llm_call", "warnings": [str(exc)], "attempt": attempt + 1})
                if attempt == 2:
                    return _needs_review(groups, str(exc))
                continue
            if not response.content.strip():
                warning = f"LLM returned no text; finish_reason={response.finish_reason}"
                self._log("warning", {"stage": "llm_call", "warnings": [warning], "attempt": attempt + 1})
                if attempt == 2:
                    return _needs_review(groups, warning)
                continue
            ok, hallucinated = validate(response.content, groups)
            if ok:
                return response.content
            self._log("warning", {"stage": "llm_validation", "warnings": hallucinated})
            if attempt < 2:
                prompt = (
                    f"{user_prompt}\n\nPrevious attempt referenced invalid IDs: {hallucinated}. "
                    "Only use IDs from the JSON above."
                )
        return "[NEEDS REVIEW]\n" + response.content

    def _build_ingestors(self) -> list[BaseIngestor]:
        ingestion = self.config.ingestion
        output_dir = self.config.output.output_dir
        kwargs = {
            "github": {"token": ingestion.github_token, "repo": ingestion.github_repo, "output_dir": output_dir, "fetch_diffs": ingestion.fetch_diffs},
            "jira": {"url": ingestion.jira_url, "email": ingestion.jira_email, "token": ingestion.jira_token, "project": ingestion.jira_project, "output_dir": output_dir},
        }
        return [get_ingestor(source, **kwargs.get(source, {})) for source in ingestion.sources]

    async def _write_outputs(self, notes: ReleaseNotes) -> None:
        formatters = self._formatters if self._formatters is not None else self._build_formatters()
        await asyncio.gather(*(formatter.write(notes) for formatter in formatters))

    def _build_formatters(self) -> list[BaseFormatter]:
        output = self.config.output
        kwargs = {
            "markdown": {"output_dir": output.output_dir},
            "slack": {"slack_webhook": output.slack_webhook},
        }
        return [get_formatter(fmt, **kwargs.get(fmt, {})) for fmt in output.formats]

    def _log(self, level: str, payload: dict) -> None:
        payload.setdefault("run_id", self.run_id)
        getattr(self.logger, level)(json.dumps(payload, default=str))


def get_logger(name: str, run_id: str) -> logging.LoggerAdapter:
    logger = logging.getLogger(name)
    return logging.LoggerAdapter(logger, {"run_id": run_id})


def validate(generated: str, input_groups: list[ChangeGroup]) -> tuple[bool, list[str]]:
    valid_ids = {g.id for g in input_groups}
    found_ids = set(re.findall(r"cg_[a-z0-9]+", generated))
    hallucinated = found_ids - valid_ids
    missing = valid_ids - found_ids
    if hallucinated or missing:
        problems = [f"invalid:{item}" for item in sorted(hallucinated)]
        problems.extend(f"missing:{item}" for item in sorted(missing))
        return False, problems
    return True, []


def _bucket_prompt(classification_label: str, version: str, groups: list[ChangeGroup]) -> str:
    json_payload = json.dumps([_payload(group) for group in groups], indent=2)
    display_label = _display_label(classification_label)
    return f"""Generate the "{display_label}" section of release notes for {version}.

Audience:
- Product managers who need to understand what work was completed.
- Developers who need enough technical specificity to recognize the change.

Changes (JSON):
{json_payload}

Rules reminder:
- One bullet per entry.
- Reference the id field at the end of every bullet: (cg_xxxxx)
- Use key_facts as the basis. If key_facts is empty, turn the title into a natural,
  readable sentence without adding facts.
- Avoid raw commit-style phrasing such as "feat:", "fix:", "chore:", "bump", or emoji
  prefixes unless they are part of a product or API name.
- Do not speculate beyond what is in the data.
"""


def _payload(group: ChangeGroup) -> dict:
    return {
        "id": group.id,
        "title": group.canonical_title,
        "ticket_id": group.source_ticket.source_id if group.source_ticket else None,
        "is_breaking": group.is_breaking,
        "authors": group.authors,
        "key_facts": group.key_facts,
    }


def _dry_bullet(group: ChangeGroup) -> str:
    prefix = "⚠️ BREAKING: " if group.is_breaking else ""
    fact = group.key_facts[0] if group.key_facts else _title_to_sentence(group.canonical_title)
    return f"- {prefix}{fact} ({group.id})"


def _bullets(content: str) -> list[str]:
    return [line.strip() for line in content.splitlines() if line.strip().startswith("-")]


def _needs_review(groups: list[ChangeGroup], reason: str) -> str:
    clean_reason = reason.replace("\n", " ")[:160]
    lines = []
    for group in groups:
        lines.append(f"- [NEEDS REVIEW] {_title_to_sentence(group.canonical_title)} ({group.id})")
    return "\n".join(lines) or f"- [NEEDS REVIEW] LLM generation failed: {clean_reason}"


def _display_label(classification_label: str) -> str:
    return {
        "features": "Features",
        "bug_fixes": "Bug fixes",
        "improvements": "Improvements",
        "breaking_changes": "Breaking changes",
    }.get(classification_label, classification_label.replace("_", " ").title())


def _title_to_sentence(title: str) -> str:
    cleaned = re.sub(r"^[^\w@/.-]+\s*", "", title or "Completed a tracked change")
    cleaned = re.sub(r"^(feat|fix|chore|refactor|perf|docs|test)(\(.+\))?!?:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    dependency = re.match(r"^bump\s+(.+?)\s+from\s+(.+?)\s+to\s+(.+?)(\s+\(#\d+\))?$", cleaned, re.IGNORECASE)
    if dependency:
        package, old, new, pr = dependency.groups()
        suffix = f" {pr}" if pr else ""
        return f"Updated {package} from {old} to {new}.{suffix}".strip()
    update_remove = re.match(r"^update\s+(.+?):\s+remove\s+(.+)$", cleaned, re.IGNORECASE)
    if update_remove:
        target, removed = update_remove.groups()
        return f"Updated {target} by removing {removed}."
    if not cleaned.endswith((".", "!", "?")):
        cleaned = f"{cleaned}."
    return cleaned[:1].upper() + cleaned[1:]
