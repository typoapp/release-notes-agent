from abc import ABC, abstractmethod

from ..schemas.release_notes import ReleaseNotes


class BaseFormatter(ABC):
    @abstractmethod
    async def write(self, notes: ReleaseNotes) -> str:
        ...
