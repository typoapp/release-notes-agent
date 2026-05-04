"""
ADK standup agent.

Workflow (enforced via the system prompt):
  1. Call schedule_tool -> wait until standup time.
  2. Call pipeline_tool(from_tag, to_tag) -> get release notes dict.
  3. (Optional) Call jira_tool(from_tag, to_tag) -> enrich with ticket list.
  4. Call slack_tool(...) -> post to Slack.
  5. Report done.

Model selection (env var RN_MODEL):
  - Any value starting with "gemini" -> native Gemini via google-adk
  - Any other value                  -> LiteLLM (supports anthropic/, openai/, etc.)

Examples:
  RN_MODEL=gemini-2.5-pro                    python main.py ...
  RN_MODEL=anthropic/claude-sonnet-4-6       python main.py ...
  RN_MODEL=openai/gpt-4o                     python main.py ...
"""

import os

from google.adk.agents import Agent
try:
    from google.adk.models.lite_llm import LiteLlmModel
except ImportError:
    from google.adk.models.lite_llm import LiteLlm as LiteLlmModel

from tools.jira_tool import jira_tool
from tools.pipeline_tool import pipeline_tool
from tools.schedule_tool import schedule_tool
from tools.slack_tool import slack_tool

_SYSTEM_PROMPT = """\
You are a release-notes standup agent. Your job runs once per day before the engineering standup.

Follow these steps in order - do not skip or reorder:

1. Call schedule_tool to wait until standup time. Do not proceed until it returns {"status": "ready"}.
2. Call pipeline_tool with the from_tag and to_tag you received. Store the returned dict.
3. If JIRA is configured (jira_tool is available), call jira_tool with the same tags.
4. Call slack_tool, passing the version, features, improvements, bug_fixes, and breaking_changes
   fields from the pipeline_tool result.
5. Report back: confirm how many entries were posted and whether any were breaking changes.

Rules:
- Never fabricate release note content. Use exactly what pipeline_tool returns.
- If pipeline_tool or slack_tool returns an error, report the error clearly and stop.
- Keep your final message concise - one or two sentences max.
"""


def _build_model():
    model_id = os.getenv("RN_MODEL", "gemini-2.5-pro")
    if model_id.startswith("gemini"):
        return model_id
    try:
        return LiteLlmModel(model_id=model_id)
    except TypeError:
        return LiteLlmModel(model=model_id)


_tools = [schedule_tool, pipeline_tool, slack_tool]
if os.getenv("JIRA_URL"):
    _tools.append(jira_tool)

standup_agent = Agent(
    name="standup_agent",
    model=_build_model(),
    description="Posts release notes to Slack before the daily engineering standup.",
    instruction=_SYSTEM_PROMPT,
    tools=_tools,
)
