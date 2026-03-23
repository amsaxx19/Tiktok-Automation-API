import json
import re
import asyncio
from urllib.parse import quote
from scrapling.fetchers import AsyncStealthySession
from scraper.base import BaseScraper
from scraper.models import VideoResult


class FacebookScraper(BaseScraper):
    platform = "facebook"
    GENERIC_AUTHOR_SEGMENTS = {"reel", "videos", "watch"}

    async def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        print(f"[Facebook] Searching for: {keyword}")
        encoded = quote(keyword)
        url = f"https://en-gb.facebook.com/watch/explore/{encoded}/"

        async with AsyncStealthySession(headless=True) as session:
            response = await session.fetch(
                url,
                wait_selector='a[href*="/videos/"], a[href*="/reel/"]',
                timeout=15000,
            )
            if response.status != 200:
                print(f"[Facebook] Failed with status {response.status}")
                return []

            # Extract video/reel URLs
            video_links = response.css('a[href*="/videos/"], a[href*="/reel/"]')

            results = []
            seen = set()

            for link in video_links:
                href = link.attrib.get("href", "")
                if not href or href in seen or href == "/watch/":
                    continue
                seen.add(href)

                full_url = f"https://www.facebook.com{href}" if href.startswith("/") else href

                # Try to get the parent container's text for stats
                result = VideoResult(
                    platform="facebook",
                    keyword=keyword,
                    video_url=full_url,
                )
                results.append(result)

                if len(results) >= max_results:
                    break

            # Extract view counts from page spans
            # Facebook shows "date · views" pattern near video cards
            spans = response.css("span")
            view_data = []
            for span in spans:
                text = span.text or ""
                if re.search(r"[\d.]+[KMB]?\s*views?", text, re.IGNORECASE):
                    views_match = re.search(r"([\d.]+)\s*([KMB]?)\s*views?", text, re.IGNORECASE)
                    if views_match:
                        view_data.append(self._parse_abbreviated(views_match.group(1), views_match.group(2)))

            # Map view counts to results (they appear in order)
            for i, views in enumerate(view_data):
                if i < len(results):
                    results[i].views = views

            print(f"[Facebook] Found {len(results)} videos, enriching concurrently...")

            # Fetch details for each video concurrently
            tasks = [self._enrich_video(session, result) for result in results]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            completed = 0
            for r in raw_results:
                if isinstance(r, Exception):
                    print(f"[Facebook] Error enriching: {r}")
                else:
                    completed += 1

        print(f"[Facebook] Enriched {completed}/{len(results)} videos")
        return results

    async def _enrich_video(self, session: AsyncStealthySession, result: VideoResult):
        response = await session.fetch(
            result.video_url,
            wait_selector='meta[property="og:title"], meta[name="description"]',
            timeout=12000,
        )
        if response.status != 200:
            return

        meta = {}
        for tag in response.css("meta"):
            prop = tag.attrib.get("property", "") or tag.attrib.get("name", "")
            content = tag.attrib.get("content", "")
            if prop and content:
                meta[prop] = content

        result.title = meta.get("og:title", "")[:100]
        result.description = meta.get("og:description", "")
        result.thumbnail = meta.get("og:image", "")

        # Prefer page identity from metadata. Reel/watch path segments are not authors.
        if not result.author:
            for key in ("og:site_name", "twitter:title"):
                candidate = (meta.get(key) or "").strip()
                if candidate and candidate.lower() not in self.GENERIC_AUTHOR_SEGMENTS:
                    result.author = candidate
                    break

        if not result.author:
            url_match = re.search(r"facebook\.com/([^/]+)/", result.video_url)
            if url_match:
                candidate = url_match.group(1)
                if candidate not in self.GENERIC_AUTHOR_SEGMENTS:
                    result.author = candidate

    @staticmethod
    def _parse_abbreviated(number_str: str, suffix: str) -> int | None:
        try:
            num = float(number_str)
            multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix.upper(), 1)
            return int(num * multiplier)
        except ValueError:
            return None
