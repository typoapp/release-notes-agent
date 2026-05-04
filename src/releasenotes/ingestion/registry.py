from importlib.metadata import entry_points

from .base import BaseIngestor

_CACHE: dict[str, type[BaseIngestor]] = {}


def get_ingestor(name: str, **kwargs) -> BaseIngestor:
    if not _CACHE:
        for ep in entry_points(group="releasenotes.ingestors"):
            _CACHE[ep.name] = ep.load()
    if name not in _CACHE:
        raise ValueError(
            f"Ingestor '{name}' not installed. "
            f"Run: pip install releasenotes-agent[{name}]"
        )
    return _CACHE[name](**kwargs)
