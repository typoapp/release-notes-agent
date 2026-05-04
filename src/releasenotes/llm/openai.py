from collections.abc import AsyncIterator

import tiktoken

from ..exceptions import LLMError
from .base import BaseLLMProvider, LLMRequest, LLMResponse


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, api_key: str = "", model: str = "gpt-4o", **kwargs):
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise LLMError("OpenAI support is not installed. Install releasenotes-agent[openai].") from exc
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key or None, **kwargs)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
            }
            if request.json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            response = await self.client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            usage = response.usage
            return LLMResponse(
                content=choice.message.content or "",
                input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
                model=response.model,
                finish_reason=choice.finish_reason or "unknown",
            )
        except Exception as exc:
            raise LLMError(str(exc)) from exc

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as exc:
            raise LLMError(str(exc)) from exc

    def count_tokens(self, text: str) -> int:
        try:
            encoding = tiktoken.encoding_for_model(self.model)
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))

    @property
    def context_window(self) -> int:
        if "gpt-4o" in self.model or "gpt-4.1" in self.model:
            return 128_000
        return 16_000

    @property
    def name(self) -> str:
        return "openai"
