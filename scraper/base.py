from abc import ABC, abstractmethod
from scrapling import StealthyFetcher
from scraper.models import VideoResult


class BaseScraper(ABC):
    platform: str = ""

    def __init__(self):
        self.fetcher = StealthyFetcher

    @abstractmethod
    def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        pass

    def fetch_page(self, url: str, wait_selector: str = None, **kwargs):
        params = {"headless": True}
        if wait_selector:
            params["wait_selector"] = wait_selector
        params.update(kwargs)
        return self.fetcher.fetch(url, **params)
