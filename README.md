# releasenotes-agent

An AI agent that automatically generates release notes by pulling completed work from GitHub and JIRA, linking related records, and using an LLM to write plain-English summaries.

Supports two modes:
- **Daily standup** — runs every morning on a schedule, covers the last 24 hours
- **Release notes** — runs after a release, covers all work between two GitHub tags and the matching JIRA fix version

Outputs are posted to a **Slack channel** and saved as a **Markdown file**.

---

## How it works

```
GitHub (commits + PRs)  ─┐
                          ├─► Normalize ─► Correlate ─► Deduplicate ─► Classify ─► LLM ─► Slack + Markdown
JIRA (Done tickets)     ─┘
```

| Stage | What it does |
|---|---|
| **Ingestion** | Fetches merged PRs and commits from GitHub, and tickets that moved to Done in JIRA, for the configured time window. With `fetch_diffs: true`, also fetches per-commit file change lists |
| **Normalization** | Standardises labels, detects conventional commit types (`feat:`, `fix:`, etc.), filters merge commits |
| **Correlation** | Links related records — e.g. a commit that references `PROJ-123` gets linked to that JIRA ticket, or a commit whose title matches a PR title is joined into the same group |
| **Deduplication** | Groups linked records into a single `ChangeGroup` so one piece of work appears once |
| **Classification** | Rule-based voting assigns each group to `features`, `bug_fixes`, `improvements`, or `breaking_changes` — deterministically, before the LLM is called |
| **LLM generation** | Sends structured JSON (titles, key facts, and changed file names when available) to the LLM; writes one plain-English bullet per group |
| **Output** | Writes `release-notes/YYYY-MM-DD.md` and posts a Slack message |

Classification happens before the LLM so the model only writes prose — it cannot change what category a change appears in.

---

## Supported providers

| Type | Options |
|---|---|
| LLM | Anthropic (Claude), OpenAI (GPT-4o), Google Gemini |
| Sources | GitHub, JIRA Cloud |
| Output | Slack webhook, Markdown file |

---

## Quick start

### 1. Install

```bash
pip install "releasenotes-agent[anthropic]"
# or: pip install "releasenotes-agent[openai]"
# or: pip install "releasenotes-agent[gemini]"
```

### 2. Configure

```bash
releasenotes init        # writes a starter releasenotes.yaml
```

Edit `releasenotes.yaml`:

```yaml
llm:
  provider: anthropic          # anthropic | openai | gemini
  model: claude-sonnet-4-6
  temperature: 0.2
  max_output_tokens: 4096

ingestion:
  sources: [github, jira]
  github_repo: "owner/repo"
  jira_url: "https://yourorg.atlassian.net"
  jira_email: "you@yourorg.com"   # required for JIRA Cloud
  jira_project: "PROJ"            # project key from the ticket URL, e.g. PROJ-123 → "PROJ"
  jira_fix_version: ""           # JIRA release name for tag-based runs; leave empty to use --to-tag value
  fetch_diffs: false              # set true to include changed file names in LLM context

output:
  formats: [markdown, slack]
  output_dir: ./release-notes

schedule:
  enabled: false
  cron: "0 8 * * 1-5"           # 8am weekdays
  timezone: UTC
  since_hours: 24
```

### 3. Set credentials

Copy `.env.example` to `.env` and fill in your keys:

```bash
# LLM — pick one
ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
# GEMINI_API_KEY=...

# GitHub
GITHUB_TOKEN=ghp_...
GITHUB_REPO=owner/repo

# JIRA Cloud
JIRA_EMAIL=you@yourorg.com
JIRA_TOKEN=<api-token-from-atlassian>   # not your password
JIRA_URL=https://yourorg.atlassian.net

# Slack (optional)
SLACK_WEBHOOK=https://hooks.slack.com/services/...
```

> **JIRA token**: go to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens) and create an API token. Do not use your account password.

### 4. Run

```bash
# Daily standup — last 24 hours
releasenotes generate --since 24h

# Daily standup — last 2 days
releasenotes generate --since 2d

# Release notes — all work between two GitHub tags (also queries JIRA by fix version)
releasenotes generate --from-tag v1.0.0 --to-tag v1.1.0

# Preview without calling the LLM
releasenotes generate --since 24h --dry-run

# Inspect what was fetched and how events were linked
releasenotes generate --since 24h --show-fetched
```

---

## Automating the morning standup

### Option A — Run as a scheduled process

Enable the built-in scheduler in `releasenotes.yaml`:

```yaml
schedule:
  enabled: true
  cron: "0 8 * * 1-5"    # 8am UTC, Monday–Friday
  timezone: UTC
  mode: date              # date = last N hours | tag = last release tag → latest tag
  since_hours: 24         # used only in date mode
```

Then start it:

```bash
releasenotes schedule
```

Keep it running with systemd, supervisord, or a container.

#### Tag mode — run automatically after each release

Set `mode: tag` to have the scheduler watch for new GitHub tags instead of covering a fixed time window:

```yaml
schedule:
  enabled: true
  cron: "0 * * * *"      # check every hour
  timezone: UTC
  mode: tag
```

On each tick the scheduler:

1. Calls `GET /repos/{owner}/{repo}/releases/latest` (falls back to `/tags`) to find the latest tag
2. Reads the last processed tag from `release-notes/.releasenotes_cache.json`
3. Skips the run if the tag has not changed
4. Runs the pipeline from the last processed tag to the new tag when a new release is detected

On the very first run (no checkpoint file), the scheduler generates notes from the tag immediately before the latest one.

### Option B — Cron job

```cron
0 8 * * 1-5  cd /path/to/project && releasenotes generate --since 24h
```

### Option C — GitHub Actions

```yaml
on:
  schedule:
    - cron: "0 8 * * 1-5"

jobs:
  standup-notes:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install "releasenotes-agent[anthropic]"
      - run: releasenotes generate --since 24h
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPO: owner/repo
          JIRA_EMAIL: ${{ secrets.JIRA_EMAIL }}
          JIRA_TOKEN: ${{ secrets.JIRA_TOKEN }}
          JIRA_URL: ${{ secrets.JIRA_URL }}
          SLACK_WEBHOOK: ${{ secrets.SLACK_WEBHOOK }}
```

---

## Generating release notes

Use `--from-tag` and `--to-tag` to generate notes for a specific release. Both GitHub and JIRA are queried.

```bash
releasenotes generate --from-tag v1.0.0 --to-tag v1.1.0
```

### How GitHub and JIRA are queried

**GitHub** uses the compare API to get all commits and merged PRs between the two tags:
```
GET /repos/{owner}/{repo}/compare/v1.0.0...v1.1.0
```

**JIRA** queries by [Fix Version](https://support.atlassian.com/jira-software-cloud/docs/plan-and-track-a-version/) — the release label you assign to tickets in JIRA:
```
project = TM AND fixVersion = "v1.1.0" AND status in (Done, Closed, Resolved)
```

By default the `--to-tag` value is used as the fix version name. If your JIRA release has a different name, set `jira_fix_version` in `releasenotes.yaml`:

```yaml
ingestion:
  jira_fix_version: "Release 1.1.0"   # overrides the --to-tag value
```

Or set it per-run via env var:
```bash
JIRA_FIX_VERSION="Release 1.1.0" releasenotes generate --from-tag v1.0.0 --to-tag v1.1.0
```

### JIRA key correlation

If your team includes JIRA ticket keys in PR titles or commit messages, the correlator links them automatically — even if you don't use JIRA fix versions. Supported patterns:

| Where | Example |
|---|---|
| PR title | `TM-123: Add calendar timezone support` |
| Commit message | `Closes TM-456` or `Fixes TM-456` |
| PR body | Any mention of `TM-123` in the description |
| Branch name | `feature/TM-789-add-export` |

When a ticket key is found and the ticket was fetched, the commit/PR and ticket are merged into a single change group. The JIRA ticket's summary and description are used as the canonical title and key facts for the LLM.

### Trigger on GitHub release

```yaml
# .github/workflows/release-notes.yml
on:
  release:
    types: [published]

jobs:
  release-notes:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install "releasenotes-agent[anthropic]"
      - run: releasenotes generate --from-tag ${{ github.event.release.target_commitish }} --to-tag ${{ github.event.release.tag_name }}
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPO: owner/repo
          JIRA_EMAIL: ${{ secrets.JIRA_EMAIL }}
          JIRA_TOKEN: ${{ secrets.JIRA_TOKEN }}
          JIRA_URL: ${{ secrets.JIRA_URL }}
          SLACK_WEBHOOK: ${{ secrets.SLACK_WEBHOOK }}
```

---

## Output

### Slack message

```
Standup Notes — May 3, 2026
8 changes completed
────────────────────────────
🚀 Features (3)
• Users can now reset their password via email link
• Added dark mode toggle to the settings page
• Introduced bulk export for invoice history

🐛 Bug Fixes (2)
• Fixed invoice generation failing for EU customers
• Resolved null pointer error on empty cart checkout

🔧 Improvements (2)
• Improved dashboard load time by 40%
• Upgraded authentication library to v3.2

⚠️ Breaking Changes (1)
• BREAKING: API v1 /users endpoint removed — migrate to /v2/users
```

### Markdown file (`release-notes/2026-05-03.md`)

```markdown
# Release Notes — 2026-05-03 (2026-05-03)

## ✨ Features
- Users can now reset their password via email link (cg_a3f9b)
...

## 🐛 Bug Fixes
...

---
*Generated by releasenotes-agent using anthropic/claude-sonnet-4-6*
*Coverage: 85% of closed tickets | Run ID: rn_8aa628be6f*
```

---

## CLI reference

### `releasenotes generate`

```
releasenotes generate --since 24h          # date-based (recommended for daily standup)
releasenotes generate --since 2d           # last 2 days
releasenotes generate --from-tag v1 --to-tag v2  # tag-based (GitHub only)
releasenotes generate --since 24h --provider openai   # override LLM provider
releasenotes generate --since 24h --format markdown   # override output format
releasenotes generate --since 24h --dry-run           # skip LLM, show classified groups
releasenotes generate --since 24h --show-fetched      # print fetched events and change groups
releasenotes generate --since 24h --dry-run --show-fetched  # combine both for full inspection
releasenotes generate --from-tag v1.0.0 --to-tag v1.1.0    # release notes (GitHub + JIRA fix version)
```

#### Flags

| Flag | Description |
|---|---|
| `--since <N>h\|<N>d` | Fetch changes from the last N hours or days, e.g. `24h` or `2d` |
| `--from-tag <tag>` | Start git tag (use with `--to-tag`); GitHub uses compare API, JIRA queries by fix version |
| `--to-tag <tag>` | End git tag (use with `--from-tag`); also used as the JIRA fix version name unless `jira_fix_version` is set |
| `--provider <name>` | Override the LLM provider (`anthropic`, `openai`, `gemini`) |
| `--format <name>` | Override output format (`markdown`, `slack`); repeatable |
| `--dry-run` | Skip the LLM call; output uses commit/PR titles directly |
| `--show-fetched` | Print two tables: all raw events fetched, and each change group after correlation and deduplication, showing which JIRA ticket links to which GitHub PRs and commits |
| `--config <path>` | Path to config file (default: `releasenotes.yaml`) |

### Other commands

```
releasenotes init        # write starter releasenotes.yaml
releasenotes schedule    # start APScheduler background process
releasenotes providers   # list installed plugins
```

---

## Configuration reference

### `releasenotes.yaml`

| Key | Default | Description |
|---|---|---|
| `llm.provider` | `anthropic` | `anthropic`, `openai`, or `gemini` |
| `llm.model` | `claude-sonnet-4-6` | Model name for the chosen provider |
| `llm.temperature` | `0.2` | Lower = more consistent output |
| `llm.max_output_tokens` | `4096` | Max tokens in LLM response |
| `ingestion.sources` | `[github, jira]` | Active ingestors |
| `ingestion.github_repo` | — | `owner/repo` format |
| `ingestion.jira_url` | — | Your Atlassian base URL |
| `ingestion.jira_email` | — | Your Atlassian account email |
| `ingestion.jira_project` | — | JIRA project key — the prefix from your ticket IDs, e.g. `TM` for `TM-123` |
| `ingestion.jira_fix_version` | — | JIRA release name for tag-based runs. Defaults to the `--to-tag` value if not set |
| `ingestion.fetch_diffs` | `false` | Fetch per-commit file change lists; enables richer LLM bullets with file names |
| `output.formats` | `[markdown, slack]` | Active formatters |
| `output.output_dir` | `./release-notes` | Where markdown files are written |
| `schedule.cron` | `0 8 * * 1-5` | Cron expression for scheduled runs |
| `schedule.timezone` | `UTC` | Timezone for the cron schedule |
| `schedule.mode` | `date` | `date` = last N hours; `tag` = detect new GitHub release tag and generate notes from last tag to new tag |
| `schedule.since_hours` | `24` | How far back each run looks (used only in `date` mode) |

### Environment variables

All secrets should be set via environment variables, not in `releasenotes.yaml`.

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `GEMINI_API_KEY` | Google Gemini API key |
| `RN_LLM_PROVIDER` | Override `llm.provider` |
| `RN_LLM_MODEL` | Override `llm.model` |
| `GITHUB_TOKEN` | GitHub personal access token |
| `GITHUB_REPO` | Override `ingestion.github_repo` |
| `JIRA_EMAIL` | Atlassian account email |
| `JIRA_TOKEN` | JIRA Cloud API token |
| `JIRA_URL` | Override `ingestion.jira_url` |
| `JIRA_FIX_VERSION` | Override `ingestion.jira_fix_version` for tag-based runs |
| `SLACK_WEBHOOK` | Incoming webhook URL |
| `RN_DEBUG_PROMPTS` | Set to `1` to write every LLM prompt to `release-notes/.debug_prompts/<run_id>/prompt_NN.txt` |

---

## Extending

The agent uses a registry pattern — new ingestors, LLM providers, and formatters are discovered via setuptools entry points.

To add a custom ingestor, implement `BaseIngestor` and register it:

```toml
# pyproject.toml
[project.entry-points."releasenotes.ingestors"]
mytracker = "mypackage.ingestors:MyTrackerIngestor"
```

Then add it to `ingestion.sources` in your config.

---

## License

MIT
