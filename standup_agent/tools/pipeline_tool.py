"""
ADK tool: run the release-notes pipeline and return structured data the agent can reason over.
The agent receives a plain dict - no internal dataclasses cross the tool boundary.
"""
import os
import sys

from google.adk.tools import FunctionTool

# Make the package importable when running from standup_agent/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from releasenotes.config import load_config
from releasenotes.pipeline.generator import ReleaseNotePipeline


async def _fetch_release_notes(from_tag: str, to_tag: str) -> dict:
    """
    Run the full release-notes pipeline between two git tags.

    Args:
        from_tag: The base git tag (e.g. 'v1.2.0').
        to_tag: The head git tag (e.g. 'v1.3.0').

    Returns a dict with keys:
        version, features, improvements, bug_fixes, breaking_changes, summary
    """
    config_path = os.getenv("RN_CONFIG", "releasenotes.yaml")
    settings = load_config(config_path)

    # Allow env-var model override for the ADK agent run
    model_override = os.getenv("RN_MODEL")
    if model_override:
        settings.llm.model = model_override

    notes = await ReleaseNotePipeline(settings).generate(from_tag, to_tag)
    return {
        "version": notes.version,
        "features": notes.features,
        "improvements": notes.improvements,
        "bug_fixes": notes.bug_fixes,
        "breaking_changes": notes.breaking_changes,
        "summary": notes.summary(),
    }


pipeline_tool = FunctionTool(func=_fetch_release_notes)
