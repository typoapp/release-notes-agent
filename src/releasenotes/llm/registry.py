from importlib.metadata import entry_points

from .base import BaseLLMProvider

_CACHE: dict[str, type[BaseLLMProvider]] = {}


def get_provider(name: str, **kwargs) -> BaseLLMProvider:
    if not _CACHE:
        for ep in entry_points(group="releasenotes.llm_providers"):
            _CACHE[ep.name] = ep.load()
    if name not in _CACHE:
        raise ValueError(
            f"LLM provider '{name}' not installed. "
            f"Run: pip install releasenotes-agent[{name}]"
        )
    return _CACHE[name](**kwargs)
