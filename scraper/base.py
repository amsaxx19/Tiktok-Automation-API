from abc import ABC, abstractmethod
from urllib.parse import quote, unquote, urlparse
import re

from scraper.models import VideoResult


class BaseScraper(ABC):
    platform: str = ""

    @abstractmethod
    async def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        pass

    @staticmethod
    def _clean_google_result_url(href: str) -> str:
        if not href:
            return ""
        cleaned = href.strip()
        if "url?q=" in cleaned:
            cleaned = cleaned.split("url?q=", 1)[1].split("&", 1)[0]
        cleaned = unquote(cleaned).split("#", 1)[0]
        if cleaned.startswith("/") or not cleaned.startswith("http"):
            return ""
        return cleaned

    async def _google_discover_urls(
        self,
        session,
        query: str,
        allowed_domains: list[str],
        url_patterns: list[str],
        max_results: int = 20,
        limit: int = 50,
    ) -> list[str]:
        url = f"https://www.google.com/search?q={quote(query)}&num={limit}"
        response = await session.fetch(url, network_idle=True, timeout=20000)
        if response.status != 200:
            return []

        urls = []
        seen = set()
        for link in response.css("a"):
            href = self._clean_google_result_url(link.attrib.get("href", ""))
            if not href:
                continue
            parsed = urlparse(href)
            netloc = (parsed.netloc or "").lower()
            if allowed_domains and not any(domain in netloc for domain in allowed_domains):
                continue
            if url_patterns and not any(pattern in href for pattern in url_patterns):
                continue
            if href in seen:
                continue
            seen.add(href)
            urls.append(href)
            if len(urls) >= max_results:
                break
        return urls

    def _make_placeholder_result(
        self,
        keyword: str,
        video_url: str,
        *,
        title: str = "",
        description: str = "",
        author: str = "",
        author_url: str = "",
        thumbnail: str = "",
        hashtags: list[str] | None = None,
    ) -> VideoResult:
        platform_label = self.platform.replace("tiktok", "TikTok").replace("youtube", "YouTube").replace("instagram", "Instagram").replace("facebook", "Facebook").replace("twitter", "X")
        fallback_title = title or f"{platform_label} video: {keyword}"
        fallback_description = description or f"Video ditemukan dari {platform_label} untuk '{keyword}'. Klik untuk menonton."
        return VideoResult(
            platform=self.platform,
            keyword=keyword,
            video_url=video_url,
            title=fallback_title[:100],
            description=fallback_description,
            author=author,
            author_url=author_url,
            thumbnail=thumbnail,
            hashtags=hashtags or re.findall(r"#(\w+)", fallback_description),
            transcript_source="fallback_discovery",
        )
