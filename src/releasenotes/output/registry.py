from importlib.metadata import entry_points

from .base import BaseFormatter

_CACHE: dict[str, type[BaseFormatter]] = {}


def get_formatter(name: str, **kwargs) -> BaseFormatter:
    if not _CACHE:
        for ep in entry_points(group="releasenotes.formatters"):
            _CACHE[ep.name] = ep.load()
    if name not in _CACHE:
        raise ValueError(
            f"Formatter '{name}' not installed. "
            f"Run: pip install releasenotes-agent[{name}]"
        )
    return _CACHE[name](**kwargs)
