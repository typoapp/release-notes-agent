# Technical Architecture

This document describes the internal flow of `releasenotes-agent` from the CLI entry point to the final Slack message, with implementation details for each stage.

---

## Table of contents

1. [Entry point — CLI](#1-entry-point--cli)
2. [Configuration loading](#2-configuration-loading)
3. [Pipeline bootstrap](#3-pipeline-bootstrap)
4. [Stage 1 — Ingestion](#4-stage-1--ingestion)
   - [GitHub ingestor](#github-ingestor)
   - [JIRA ingestor](#jira-ingestor)
5. [Stage 2 — Normalization](#5-stage-2--normalization)
6. [Stage 3 — Correlation](#6-stage-3--correlation)
7. [Stage 4 — Deduplication](#7-stage-4--deduplication)
8. [Stage 5 — Classification](#8-stage-5--classification)
9. [Stage 6 — LLM generation](#9-stage-6--llm-generation)
10. [Stage 7 — Output](#10-stage-7--output)
    - [Markdown formatter](#markdown-formatter)
    - [Slack formatter](#slack-formatter)
11. [Data schemas](#11-data-schemas)
12. [Error handling](#12-error-handling)
13. [Scheduler](#13-scheduler)

---

## Full flow diagram

```
CLI (cli.py)
  └── load_config()                         # merge YAML + .env + env vars
  └── ReleaseNotePipeline.generate()
        │
        ├── _ingest()                        # asyncio.gather across ingestors
        │     ├── GitHubIngestor.fetch()     # commits + merged PRs
        │     └── JIRAIngestor.fetch()       # Done tickets
        │                                    → list[ChangeEvent]
        │
        ├── normalize()                      # standardise labels, filter merges
        │                                    → list[ChangeEvent]
        │
        ├── correlate()                      # link tickets ↔ commits ↔ PRs
        │                                    → list[ChangeEvent] (with linked_ids populated)
        │
        ├── deduplicate()                    # BFS graph → ChangeGroups
        │                                    → list[ChangeGroup]
        │
        ├── classify()                       # signal-weighted voting
        │                                    → list[ChangeGroup] (with classification set)
        │
        ├── _render_notes()                  # LLM prose generation per bucket
        │     └── _generate_bucket()        # per category: features / bug_fixes / etc.
        │           ├── plan_calls()         # chunk groups by token budget
        │           └── _call_llm()         # up to 3 retries + ID validation
        │                                    → ReleaseNotes
        │
        └── _write_outputs()                # asyncio.gather across formatters
              ├── MarkdownFormatter.write()  # ./release-notes/YYYY-MM-DD.md
              └── SlackFormatter.write()    # POST to webhook
```

---

## 1. Entry point — CLI

**File:** [src/releasenotes/cli.py](src/releasenotes/cli.py)

The CLI is a [Click](https://click.palletsprojects.com/) group with four commands. The primary command is `generate`.

```
releasenotes generate --since 24h
```

### `--since` mode (daily standup)

When `--since` is passed:

```python
since_dt = datetime.now(UTC) - timedelta(hours=N)   # e.g. now - 24h
from_ref = since_dt.isoformat()                      # "2026-05-02T08:00:00+00:00"
to_ref   = datetime.now(UTC).date().isoformat()      # "2026-05-03"
```

`to_ref` becomes the file name (e.g. `release-notes/2026-05-03.md`) and the `version` field in all output.

### `--from-tag / --to-tag` mode

Git tags are passed through directly. The GitHub ingestor detects them by checking whether the string starts with a `YYYY-MM-DD` pattern — if not, it uses the `/compare/{from}...{to}` API. JIRA cannot accept git tags and will skip with a warning.

### Mutual exclusivity

The CLI enforces that you pass either `--since` OR both `--from-tag` and `--to-tag` — never both.

---

## 2. Configuration loading

**File:** [src/releasenotes/config.py](src/releasenotes/config.py)

Loading order (later values override earlier ones):

1. `releasenotes.yaml` (parsed with PyYAML)
2. `.env` file (via python-dotenv)
3. `.env.local` file
4. `ENV_MAP` — specific env vars mapped to nested config keys:

```python
ENV_MAP = {
    "RN_LLM_PROVIDER": ("llm", "provider"),
    "ANTHROPIC_API_KEY": ("llm", "api_key"),   # auto-detected by provider
    "GITHUB_TOKEN":      ("ingestion", "github_token"),
    "JIRA_EMAIL":        ("ingestion", "jira_email"),
    "JIRA_TOKEN":        ("ingestion", "jira_token"),
    "SLACK_WEBHOOK":     ("output", "slack_webhook"),
    ...
}
```

The merged dictionary is validated by Pydantic's `Settings.model_validate()`. The resulting `Settings` object is passed to every stage of the pipeline.

**Key config sections:**

| Class | Purpose |
|---|---|
| `LLMConfig` | Provider, model, API key, temperature, token budget |
| `IngestionConfig` | Sources list, credentials for GitHub and JIRA |
| `OutputConfig` | Formats list, output directory, Slack webhook |
| `ScheduleConfig` | Cron expression, timezone, `since_hours` |

---

## 3. Pipeline bootstrap

**File:** [src/releasenotes/pipeline/generator.py](src/releasenotes/pipeline/generator.py)

`ReleaseNotePipeline.__init__()` sets up:
- A unique `run_id` (e.g. `rn_8aa628be6f`) used in all log lines and the output footer
- A structured JSON logger (`logging.LoggerAdapter`)
- Optional dependency injection slots for `ingestors`, `llm_provider`, `formatters` (used in tests)

`generate(from_ref, to_ref)` runs all stages sequentially and returns a `ReleaseNotes` dataclass.

---

## 4. Stage 1 — Ingestion

**Files:** [src/releasenotes/ingestion/github.py](src/releasenotes/ingestion/github.py), [src/releasenotes/ingestion/jira.py](src/releasenotes/ingestion/jira.py)

Both ingestors run **concurrently** via `asyncio.gather()`. Each returns `list[ChangeEvent]`. If one fails, the other's results are still used.

```python
results = await asyncio.gather(
    github_ingestor.fetch(from_ref, to_ref),
    jira_ingestor.fetch(from_ref, to_ref),
    return_exceptions=True,
)
```

The pipeline only raises an error if **every** ingestor fails. A single failure is logged as a warning and the pipeline continues.

### GitHub ingestor

Detects mode from `from_ref`:

**Date-based mode** (`from_ref` matches `YYYY-MM-DD...`):
```
GET /repos/{owner}/{repo}/commits?since={from_ref}&until={to_ref}&per_page=100
GET /repos/{owner}/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100
```
PRs are filtered by `merged_at >= from_ref`.

**Tag-based mode** (`from_ref` is a git tag):
```
GET /repos/{owner}/{repo}/compare/{from_tag}...{to_tag}?per_page=100
GET /repos/{owner}/{repo}/pulls?state=closed  (filtered by the date window derived from the compare response)
```

Both modes produce `ChangeEvent` objects with `source_type="commit"` or `source_type="pr"`.

Each HTTP call uses tenacity retry logic: up to 5 attempts, exponential backoff (2s→60s), triggered on `429` or `5xx` responses.

Raw API responses are cached to `.raw/github/YYYY-MM-DD/page_N.json` for debugging.

### JIRA ingestor

Requires `from_ref` to be a date string. If a git tag is passed, it logs a warning and returns `[]`.

Uses **Basic Auth** (`email:api_token`) as required by JIRA Cloud:
```python
auth = (email, token)   # httpx encodes as Basic base64(email:token)
```

JQL query:
```
project = PROJ
  AND status in (Done, Closed, Resolved)
  AND updated >= "2026-05-02 08:57"
ORDER BY updated DESC
```

The date is formatted as `YYYY-MM-DD HH:mm` (JIRA's expected format, converted from the ISO 8601 `from_ref`).

API endpoint: `GET /rest/api/3/search/jql` (paginated, 100 issues per page).

Produces `ChangeEvent` objects with `source_type="ticket"`. JIRA ADF (Atlassian Document Format) descriptions are converted to plain text via `adf_to_text()`.

---

## 5. Stage 2 — Normalization

**File:** [src/releasenotes/pipeline/normalizer.py](src/releasenotes/pipeline/normalizer.py)

Takes `list[ChangeEvent]`, returns a cleaned `list[ChangeEvent]`.

What it does to each event:

| Operation | Detail |
|---|---|
| **Label normalisation** | Maps variants → canonical: `bugfix`→`bug`, `enhancement`→`feat`, etc. via `LABEL_MAP` |
| **Conventional commit detection** | Regex `^(feat\|fix\|chore\|...)(scope)?!: ` on the commit title; sets `conventional_type` |
| **Breaking commit detection** | `!` in conventional prefix OR `BREAKING CHANGE:` in body → `conventional_type = "breaking"` |
| **Email normalisation** | Lowercased, `+github` suffix stripped |
| **Merge commit filtering** | Commits matching `^Merge (branch\|pull request\|remote)` are dropped entirely |

---

## 6. Stage 3 — Correlation

**File:** [src/releasenotes/pipeline/correlator.py](src/releasenotes/pipeline/correlator.py)

Builds a **graph of edges** between `ChangeEvent` objects by populating each event's `linked_ids` dict:

```python
event.linked_ids = {
    "tickets": [{"id": "ce_abc123", "confidence": 0.95, "strategy": "explicit_body"}],
    "prs":     [{"id": "ce_def456", "confidence": 0.90, "strategy": "pr_number"}],
}
```

### Linking strategies (in priority order)

| Strategy | Trigger | Confidence |
|---|---|---|
| `explicit_body` | JIRA key (`PROJ-123`) found in commit/PR body | 0.95 |
| `branch` | JIRA key in PR branch name (e.g. `feature/PROJ-123-...`) | 0.85 |
| `pr_text` | JIRA key in PR title or first 500 chars of body | 0.80 |
| `pr_number` | Commit message references `closes #42`, `(#42)`, `PR #42` | 0.90 |
| `author_time` | Same author email + overlapping timestamps (fallback) | 0.40 |
| `semantic` | Token-based cosine similarity ≥ 0.82 on titles (opt-in) | varies |

Edges are **bidirectional** — both sides of a link are updated. Duplicate edges are skipped.

The semantic linking (`use_semantic_linking: true`) uses tiktoken tokens as a 64-dimensional sparse vector with cosine similarity. It is disabled by default because it can produce false positives.

---

## 7. Stage 4 — Deduplication

**File:** [src/releasenotes/pipeline/deduplicator.py](src/releasenotes/pipeline/deduplicator.py)

Converts the flat `list[ChangeEvent]` (with edges) into `list[ChangeGroup]` where each group represents one piece of completed work.

### Algorithm

1. Build an **undirected graph** from all `linked_ids` edges
2. BFS from each unvisited node → finds all connected components
3. Each component becomes one `ChangeGroup`

### Within each group

**Canonical title selection** (priority order):
1. JIRA ticket title (most user-facing)
2. PR title
3. First non-noisy commit title
4. First commit title

**Noise scoring:** Commits matching patterns like `^WIP`, `^fixup!`, `^bump (version|deps)`, `^update (changelog|readme)` get `noise_score = 0.9`. If a group contains only noisy commits and no ticket, the group itself gets `noise_score = 0.9` and is filtered out before the LLM call.

**Key facts extraction:** Sentences from the ticket/PR body that contain action verbs (`add`, `fix`, `update`, `enable`, etc.) and are longer than 8 words are extracted (up to 5). These are sent to the LLM as structured hints.

---

## 8. Stage 5 — Classification

**File:** [src/releasenotes/pipeline/classifier.py](src/releasenotes/pipeline/classifier.py)

Assigns each `ChangeGroup` a category using a **signal-weighted voting system** — no LLM involved.

### Signals and weights

| Signal | Weight | Example |
|---|---|---|
| Ticket issue type | 0.90 | JIRA bug → `bug_fixes` |
| Conventional commit type | 0.80 | `feat:` → `features` |
| Label | 0.70 | label `breaking-change` → `breaking_changes` |
| Title keyword | 0.50 | title contains "add" → `features` |

All weights for a category are summed. The category with the highest total wins. If no signal scores ≥ 0.5 and there is no ticket, it defaults to `improvements`.

### Breaking change detection

Runs separately and adds 1.0 to `breaking_changes` if any of these are found:
- Conventional commit with `!` marker or `feat!:` prefix
- `BREAKING CHANGE:` in commit body
- PR label `breaking-change`, `semver:major`, or `breaking`
- Ticket label `migration-required`
- Diff contains `DROP COLUMN`, `RENAME COLUMN`, or a removed `@GetMapping`/`@PostMapping` annotation

---

## 9. Stage 6 — LLM generation

**File:** [src/releasenotes/pipeline/generator.py](src/releasenotes/pipeline/generator.py)

Groups with `noise_score ≥ 0.9` are filtered out. Remaining groups are split into four buckets: `features`, `improvements`, `bug_fixes`, `breaking_changes`.

### Token budget

Before calling the LLM, `plan_calls()` estimates whether all groups fit in one call:

```
usable_tokens = provider.context_window - max_output_tokens - 2000 (safety buffer)
estimated_per_group = 300 tokens input + 350 tokens output
```

If the total exceeds the budget, groups are chunked into batches of up to 20.

### Prompt structure

Two parts sent to the LLM:

**System prompt** (constant, sets rules):
- One bullet per change group
- Every bullet must end with the group ID: `(cg_xxxxx)`
- Breaking changes must start with `⚠️ BREAKING:`
- No hallucination — only use facts from the provided JSON
- Write for a technical audience: PMs and developers

**User prompt** (per bucket, per chunk):
```
Generate the "Features" section of release notes for 2026-05-03.

Changes (JSON):
[
  {
    "id": "cg_a3f9b",
    "title": "Add Google SSO login",
    "ticket_id": "PROJ-412",
    "is_breaking": false,
    "authors": ["alice"],
    "key_facts": ["Users can now sign in using their Google account."]
  },
  ...
]
```

### Retry and validation

After each LLM response, the generated text is validated:

```python
valid_ids   = {g.id for g in input_groups}        # e.g. {"cg_a3f9b", "cg_c7d2e"}
found_ids   = re.findall(r"cg_[a-z0-9]+", output) # extracted from response
hallucinated = found_ids - valid_ids               # IDs that don't exist
missing      = valid_ids - found_ids               # groups not mentioned
```

If validation fails, the prompt is retried up to 3 times with the hallucinated IDs appended as a correction note. After 3 failures, bullets are prefixed `[NEEDS REVIEW]`.

### Multi-chunk reduce

If a bucket required multiple chunks, the partial outputs are either:
- **Concatenated** if the total would exceed the output token budget
- **Reduced** — sent back to the LLM with a "merge and deduplicate" instruction

---

## 10. Stage 7 — Output

Both formatters run **concurrently** via `asyncio.gather()`.

### Markdown formatter

**File:** [src/releasenotes/output/markdown.py](src/releasenotes/output/markdown.py)

Writes to `{output_dir}/{version}.md`. For `--since` mode, `version` is `YYYY-MM-DD`, so the file is e.g. `release-notes/2026-05-03.md`.

Structure:
```markdown
# Release Notes — 2026-05-03 (2026-05-03)

## ✨ Features
- Users can now sign in with Google SSO (cg_a3f9b)

## 🐛 Bug Fixes
- Fixed invoice generation failing for EU customers (cg_b1c2d)

## 🔧 Improvements
...

## ⚠️ Breaking Changes
...

---
*Generated by releasenotes-agent 0.1.0 using anthropic/claude-sonnet-4-6*
*Coverage: 85% of closed tickets | Run ID: rn_8aa628be6f*
```

Coverage % = tickets with a linked commit or PR ÷ total tickets fetched.

### Slack formatter

**File:** [src/releasenotes/output/slack.py](src/releasenotes/output/slack.py)

POSTs [Block Kit](https://api.slack.com/block-kit) JSON to the configured webhook URL via `httpx.AsyncClient`.

`(cg_xxxxx)` IDs are stripped from bullets before sending — they are internal identifiers not useful to Slack readers.

Payload structure:
```json
{
  "blocks": [
    { "type": "header",  "text": { "type": "plain_text", "text": "Standup Notes — May 3, 2026" }},
    { "type": "section", "text": { "type": "mrkdwn",     "text": "*8 changes completed*" }},
    { "type": "divider" },
    { "type": "section", "text": { "type": "mrkdwn",     "text": "*🚀 Features (3)*\n• ..." }},
    { "type": "section", "text": { "type": "mrkdwn",     "text": "*🐛 Bug Fixes (2)*\n• ..." }}
  ]
}
```

If `SLACK_WEBHOOK` is not set, the formatter logs a warning and returns `"slack:skipped"` without crashing.

---

## 11. Data schemas

**Files:** [src/releasenotes/schemas/](src/releasenotes/schemas/)

### `ChangeEvent`

Represents a single raw record — one commit, one PR, or one JIRA ticket.

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Internal ID: `ce_xxxxxxxx` |
| `source_type` | `"ticket" \| "commit" \| "pr"` | Record type |
| `source_system` | `"github" \| "jira"` | Origin system |
| `source_id` | `str` | Original ID (SHA, PR number, JIRA key) |
| `title` | `str` | Commit subject line, PR title, or JIRA summary |
| `body` | `str` | Full description / commit body |
| `author_email` | `str` | Normalised author email |
| `status` | `str` | `done`, `in_progress`, `open`, `wont_fix` |
| `conventional_type` | `str \| None` | `feat`, `fix`, `breaking`, etc. |
| `is_merge` | `bool` | True for merge commits (filtered out) |
| `noise_score` | `float` | 0.0 = signal, 0.9 = noise |
| `linked_ids` | `dict` | Edges: `{"tickets": [...], "prs": [...], "commits": [...]}` |
| `raw_payload` | `dict` | Full original API response |

### `ChangeGroup`

Represents one deduplicated piece of work, ready for classification and LLM generation.

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Internal ID: `cg_xxxxxxxx` |
| `canonical_title` | `str` | Best available title (ticket > PR > commit) |
| `canonical_body` | `str \| None` | Best available description |
| `canonical_labels` | `list[str]` | Merged normalised labels from all members |
| `source_ticket` | `ChangeEvent \| None` | The linked JIRA ticket, if any |
| `source_commits` | `list[ChangeEvent]` | All linked commits |
| `source_prs` | `list[ChangeEvent]` | All linked PRs |
| `classification` | `str \| None` | `features`, `bug_fixes`, `improvements`, `breaking_changes` |
| `classification_confidence` | `float` | Sum of winning signal weights |
| `is_breaking` | `bool` | True if breaking signals were detected |
| `breaking_signals` | `list[str]` | Human-readable list of what triggered breaking detection |
| `authors` | `list[str]` | Sorted author names from all members |
| `noise_score` | `float` | 0.9 = skip this group |
| `key_facts` | `list[str]` | Up to 5 extracted sentences from ticket/PR body |

### `ReleaseNotes`

The final output object.

| Field | Type | Description |
|---|---|---|
| `version` | `str` | Tag or date string used as file name |
| `from_tag` | `str` | Start of the window |
| `to_tag` | `str` | End of the window |
| `generated_at` | `str` | ISO 8601 UTC timestamp |
| `features` | `list[str]` | LLM-generated bullets |
| `improvements` | `list[str]` | LLM-generated bullets |
| `bug_fixes` | `list[str]` | LLM-generated bullets |
| `breaking_changes` | `list[str]` | LLM-generated bullets |
| `coverage_pct` | `float` | Fraction of fetched tickets linked to a commit/PR |
| `run_id` | `str` | Unique run identifier for log correlation |
| `llm_provider` | `str` | e.g. `anthropic` |
| `llm_model` | `str` | e.g. `claude-sonnet-4-6` |

---

## 12. Error handling

| Failure | Behaviour |
|---|---|
| One ingestor fails | Logs a warning, continues with other ingestors' data |
| All ingestors fail | Raises `RuntimeError`, pipeline aborts |
| LLM returns empty | Retried up to 3× with the same prompt |
| LLM returns hallucinated IDs | Retried up to 3× with a correction appended to the prompt |
| LLM fails 3 times | Bullets prefixed `[NEEDS REVIEW]`, pipeline continues |
| Slack webhook missing | Logs warning, returns `"slack:skipped"`, pipeline continues |
| Slack POST fails | `response.raise_for_status()` propagates the error |
| Invalid YAML config | `ConfigError` raised before pipeline starts |

All stages emit structured JSON logs with `run_id` so errors can be correlated:

```json
{"stage": "llm_validation", "warnings": ["invalid:cg_zzz999"], "run_id": "rn_8aa628be6f"}
```

---

## 13. Scheduler

**File:** [src/releasenotes/scheduler/cron.py](src/releasenotes/scheduler/cron.py)

Uses [APScheduler](https://apscheduler.readthedocs.io/) with an `AsyncIOScheduler` and a `CronTrigger`.

On each tick:

```python
now   = datetime.now(UTC)
since = now - timedelta(hours=config.schedule.since_hours)  # default: 24h
today = now.date().isoformat()

await ReleaseNotePipeline(config).generate(since.isoformat(), today)
```

The scheduler runs in a foreground `asyncio` event loop and sleeps in 1-hour intervals between checks. Stop with `Ctrl-C` — `scheduler.shutdown()` is called in the `finally` block.

Default schedule: `0 8 * * 1-5` — 8:00 AM UTC, Monday to Friday.
