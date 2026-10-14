from __future__ import annotations

from abc import ABC, abstractmethod

from jipsooni.models import Notice


class NoticeSource(ABC):
    name: str

    @abstractmethod
    def fetch(self) -> list[Notice]:
        raise NotImplementedError
