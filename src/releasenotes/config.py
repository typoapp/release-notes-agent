import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
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
    github_repo: str = ""
    use_semantic_linking: bool = False
    fetch_diffs: bool = False


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
    "GITHUB_REPO": ("ingestion", "github_repo"),
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
  max_output_tokens: 4096

ingestion:
  sources: [github, jira]
  github_repo: "owner/repo"    # e.g. acme/backend
  jira_url: "https://yourorg.atlassian.net"
  jira_project: "PROJ"         # JIRA project key (prefix from ticket IDs, e.g. TM for TM-123)
  jira_fix_version: ""         # optional: JIRA release name when using --from-tag/--to-tag (defaults to to-tag value)

output:
  formats: [markdown, slack]
  output_dir: ./release-notes

schedule:
  enabled: false
  cron: "0 8 * * 1-5"          # 8am weekdays
  timezone: UTC
  mode: date                    # date = last N hours | tag = last release tag → latest tag
  since_hours: 24               # used only in date mode
"""
