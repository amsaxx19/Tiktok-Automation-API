from abc import ABC, abstractmethod
import httpx
from scraper.models import VideoResult


class BaseScraper(ABC):
    platform: str = ""

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

    def __init__(self):
        self.fetcher = StealthyFetcher

    @abstractmethod
    def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        pass

    def fetch_page(self, url: str, **kwargs) -> httpx.Response:
        try:
            return self.client.get(url, **kwargs)
        except (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout) as e:
            print(f"[{self.platform}] Connection error: {e}")
            # Return a fake 503 response so callers can handle gracefully
            return httpx.Response(status_code=503, text="")
