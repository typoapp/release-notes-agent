import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigError


class LLMConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key: str = ""
    temperature: float = 0.2
    max_output_tokens: int = 4096


class IngestionConfig(BaseModel):
    sources: list[str] = Field(default_factory=lambda: ["github", "jira"])
    jira_url: str = ""
    jira_email: str = ""
    jira_token: str = ""
    jira_project: str = ""
    jira_fix_version: str = ""   # when set, used as fixVersion in tag-based runs; defaults to to_tag
    github_token: str = ""
    github_repo: str = ""        # single-repo (backward compat); prefer github_repos
    github_repos: list[str] = Field(default_factory=list)  # one or more owner/repo strings
    use_semantic_linking: bool = False
    fetch_diffs: bool = False
    fetch_pr_diffs: bool = False  # fetch per-PR file patches for richer LLM context (higher cost)

    @model_validator(mode="after")
    def _coerce_repos(self) -> "IngestionConfig":
        # Fold single github_repo into github_repos so the rest of the code only reads one field.
        if self.github_repo and self.github_repo not in self.github_repos:
            self.github_repos = [self.github_repo] + list(self.github_repos)
        return self


class OutputConfig(BaseModel):
    formats: list[str] = Field(default_factory=lambda: ["markdown", "slack"])
    output_dir: str = "./release-notes"
    slack_webhook: str = ""


class ScheduleConfig(BaseModel):
    enabled: bool = False
    cron: str = "0 8 * * 1-5"
    timezone: str = "UTC"
    mode: str = "date"     # "date" = last N hours | "tag" = last release tag → latest tag
    since_hours: int = 24  # used only in date mode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)

    def safe_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        for section, key in [
            ("llm", "api_key"),
            ("ingestion", "jira_email"),
        ("ingestion", "jira_token"),
            ("ingestion", "github_token"),
            ("output", "slack_webhook"),
        ]:
            if data.get(section, {}).get(key):
                data[section][key] = "***"
        return data


ENV_MAP = {
    "RN_LLM_PROVIDER": ("llm", "provider"),
    "RN_LLM_MODEL": ("llm", "model"),
    "JIRA_EMAIL": ("ingestion", "jira_email"),
    "JIRA_TOKEN": ("ingestion", "jira_token"),
    "JIRA_URL": ("ingestion", "jira_url"),
    "JIRA_FIX_VERSION": ("ingestion", "jira_fix_version"),
    "GITHUB_TOKEN": ("ingestion", "github_token"),
    "GITHUB_REPO": ("ingestion", "github_repo"),   # single-repo override
    "SLACK_WEBHOOK": ("output", "slack_webhook"),
}


def load_config(path: str = "releasenotes.yaml") -> Settings:
    if Path(".env").is_file():
        load_dotenv(".env")
    load_dotenv(".env.local")
    data: dict[str, Any] = {}
    config_path = Path(path)
    if config_path.exists():
        try:
            loaded = yaml.safe_load(config_path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"{path} must contain a YAML mapping")
        data = loaded
    _apply_env(data)
    return Settings.model_validate(data)


def _apply_env(data: dict[str, Any]) -> None:
    provider_key = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }.get(os.getenv("RN_LLM_PROVIDER", _get(data, "llm", "provider") or "anthropic"))
    if provider_key and os.getenv(provider_key):
        _set(data, "llm", "api_key", os.getenv(provider_key, ""))
    for env, (section, key) in ENV_MAP.items():
        if os.getenv(env) is not None:
            _set(data, section, key, os.getenv(env, ""))
    # GITHUB_REPOS accepts a comma-separated list and overrides github_repos entirely.
    github_repos_env = os.getenv("GITHUB_REPOS")
    if github_repos_env:
        repos = [r.strip() for r in github_repos_env.split(",") if r.strip()]
        if repos:
            _set(data, "ingestion", "github_repos", repos)


def _get(data: dict[str, Any], section: str, key: str) -> Any:
    return data.get(section, {}).get(key)


def _set(data: dict[str, Any], section: str, key: str, value: Any) -> None:
    data.setdefault(section, {})
    data[section][key] = value


def starter_yaml() -> str:
    return """llm:
  provider: anthropic          # anthropic | openai | gemini
  model: claude-sonnet-4-6     # override if needed
  temperature: 0.2
  max_output_tokens: 8192      # increase for releases with many changes

ingestion:
  sources: [github, jira]
  github_repos:                  # one or more owner/repo values; use a single item for one repo
    - "owner/repo"               # e.g. acme/backend
  github_token: ""             # GitHub personal access token (set via GITHUB_TOKEN env var)
  jira_url: "https://yourorg.atlassian.net"
  jira_email: ""               # Atlassian account email for JIRA Cloud Basic Auth (set via JIRA_EMAIL env var)
  jira_token: ""               # JIRA API token (set via JIRA_TOKEN env var)
  jira_project: "PROJ"         # JIRA project key (prefix from ticket IDs, e.g. TM for TM-123)
  jira_fix_version: ""         # optional: JIRA release name when using --from-tag/--to-tag (defaults to to-tag value)
  use_semantic_linking: false  # experimental: link commits to tickets via title similarity
  fetch_diffs: false           # fetch changed file lists per commit (slower, adds detail to notes)
  fetch_pr_diffs: false        # fetch PR code patches for higher LLM accuracy (higher token cost)

output:
  formats: [markdown, slack]
  output_dir: ./release-notes
  slack_webhook: ""            # Slack incoming webhook URL (set via SLACK_WEBHOOK env var)

schedule:
  enabled: false
  cron: "0 8 * * 1"            # 8am every Monday
  timezone: UTC
  mode: tag                    # tag = last release tag → latest tag | date = last N hours
  since_hours: 24               # used only in date mode
"""
