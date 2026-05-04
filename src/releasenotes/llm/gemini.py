from collections.abc import AsyncIterator
from typing import Any

import tiktoken

from ..exceptions import LLMError
from .base import BaseLLMProvider, LLMRequest, LLMResponse


class GeminiProvider(BaseLLMProvider):
    def __init__(self, api_key: str = "", model: str = "gemini-2.5-pro", **kwargs):
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise LLMError("Gemini support is not installed. Install releasenotes-agent[gemini].") from exc
        self.model = model
        self.client = genai.Client(api_key=api_key or None, **kwargs)
        self.types = types

    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=request.user,
                config=self.types.GenerateContentConfig(
                    system_instruction=request.system,
                    max_output_tokens=request.max_tokens,
                    temperature=request.temperature,
                    automatic_function_calling=self.types.AutomaticFunctionCallingConfig(disable=True),
                    thinking_config=self.types.ThinkingConfig(
                        include_thoughts=False,
                        thinking_budget=_thinking_budget(self.model),
                    ),
                ),
            )
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        try:
            text = _response_text(response)
            return LLMResponse(
                content=text,
                input_tokens=self.count_tokens(f"{request.system}\n\n{request.user}"),
                output_tokens=self.count_tokens(text),
                model=self.model,
                finish_reason=_finish_reason(response),
            )
        except Exception as exc:
            raise LLMError(str(exc)) from exc

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        response = await self.complete(request)
        yield response.content

    def count_tokens(self, text: str) -> int:
        return len(tiktoken.get_encoding("cl100k_base").encode(text))

    @property
    def context_window(self) -> int:
        return 1_000_000

    @property
    def name(self) -> str:
        return "gemini"


def _response_text(response: Any) -> str:
    try:
        text = response.text
    except Exception:
        text = ""
    if text:
        return text

    parts: list[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", "")
            if part_text:
                parts.append(part_text)
    return "".join(parts)


def _finish_reason(response: Any) -> str:
    reasons = []
    for candidate in getattr(response, "candidates", []) or []:
        reason = getattr(candidate, "finish_reason", None)
        if reason is not None:
            reasons.append(getattr(reason, "name", str(reason)))
    return ",".join(reasons) or "unknown"


def _thinking_budget(model: str) -> int:
    lowered = model.lower()
    if "2.5-pro" in lowered:
        return 128
    if "2.5-flash" in lowered:
        return 0
    return 0
