from abc import ABC, abstractmethod

from ..schemas.change_event import ChangeEvent


class BaseIngestor(ABC):
    @abstractmethod
    async def fetch(self, from_tag: str, to_tag: str) -> list[ChangeEvent]:
        """Fetch all relevant events between two git tags."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify credentials and connectivity."""
        ...
