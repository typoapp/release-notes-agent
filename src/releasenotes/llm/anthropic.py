from collections.abc import AsyncIterator

import tiktoken

from ..exceptions import LLMError
from .base import BaseLLMProvider, LLMRequest, LLMResponse


class AnthropicProvider(BaseLLMProvider):
    def __init__(self, api_key: str = "", model: str = "claude-3-5-sonnet-20240620", **kwargs):
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise LLMError("Anthropic support is not installed. Install releasenotes-agent[anthropic].") from exc
        self.model = model
        self.client = AsyncAnthropic(api_key=api_key or None, **kwargs)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            response = await self.client.messages.create(
                model=self.model,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
            content = "".join(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            )
            usage = response.usage
            return LLMResponse(
                content=content,
                input_tokens=getattr(usage, "input_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0),
                model=response.model,
                finish_reason=response.stop_reason or "unknown",
            )
        except Exception as exc:
            raise LLMError(str(exc)) from exc

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        try:
            async with self.client.messages.stream(
                model=self.model,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as exc:
            raise LLMError(str(exc)) from exc

    def count_tokens(self, text: str) -> int:
        # cl100k_base slightly under-counts for Claude; add 10% safety buffer
        return int(len(tiktoken.get_encoding("cl100k_base").encode(text)) * 1.1)

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def name(self) -> str:
        return "anthropic"
