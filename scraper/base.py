from abc import ABC, abstractmethod
from scraper.models import VideoResult


class BaseScraper(ABC):
    platform: str = ""

    @abstractmethod
    async def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        pass
