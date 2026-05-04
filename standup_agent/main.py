"""
Entry point for the ADK standup agent.

Usage:
    cd standup_agent
    python main.py --from-tag v1.2.0 --to-tag v1.3.0

Environment (set in .env or shell):
    RN_MODEL           Model string, e.g. "gemini-2.5-pro" or "anthropic/claude-sonnet-4-6"
    RN_CONFIG          Path to releasenotes.yaml (default: ../releasenotes.yaml)
    RN_STANDUP_TIME    HH:MM in 24h format (default: 09:45)
    RN_STANDUP_TZ      IANA timezone (default: UTC)
    RN_OUTPUT_DIR      Where to write .raw/ cache (default: ./release-notes)
    SLACK_WEBHOOK_URL  Incoming webhook URL
    GITHUB_TOKEN       GitHub personal access token
    JIRA_URL / JIRA_TOKEN / JIRA_PROJECT  (optional)
    GEMINI_API_KEY     Required if using Gemini natively
    ANTHROPIC_API_KEY  Required if RN_MODEL starts with "anthropic/"
    OPENAI_API_KEY     Required if RN_MODEL starts with "openai/"
"""
# ruff: noqa: E402

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load secrets from standup_agent/.env
load_dotenv(Path(__file__).parent / ".env")

# Add project root src/ to path so `releasenotes` package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from agent import standup_agent


async def run(from_tag: str, to_tag: str) -> None:
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="standup_agent",
        user_id="system",
    )

    runner = Runner(
        agent=standup_agent,
        app_name="standup_agent",
        session_service=session_service,
    )

    user_message = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=f"Post release notes from {from_tag} to {to_tag}.")],
    )

    print(f"[standup_agent] Starting run: {from_tag} -> {to_tag}")
    async for event in runner.run_async(
        user_id="system",
        session_id=session.id,
        new_message=user_message,
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    print(f"[standup_agent] {part.text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ADK standup agent - posts release notes to Slack.")
    parser.add_argument("--from-tag", required=True, help="Base git tag (e.g. v1.2.0)")
    parser.add_argument("--to-tag", required=True, help="Head git tag (e.g. v1.3.0)")
    args = parser.parse_args()
    asyncio.run(run(args.from_tag, args.to_tag))


if __name__ == "__main__":
    main()
