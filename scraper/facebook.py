import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
from bs4 import BeautifulSoup
from scraper.base import BaseScraper
from scraper.models import VideoResult


class FacebookScraper(BaseScraper):
    platform = "facebook"
    GENERIC_AUTHOR_SEGMENTS = {"reel", "videos", "watch"}

    def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        print(f"[Facebook] Searching for: {keyword}")

        response = self.fetch_page(
            url,
            wait_selector='a[href*="/videos/"], a[href*="/reel/"]',
            timeout=15000,
        )
        if response.status != 200:
            print(f"[Facebook] Failed with status {response.status}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        urls = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "facebook.com" in href and ("/videos/" in href or "/reel/" in href or "/watch" in href):
                if "url?q=" in href:
                    href = href.split("url?q=")[1].split("&")[0]
                href = href.split("#")[0]
                if href not in seen:
                    seen.add(href)
                    urls.append(href)
            if len(urls) >= max_results:
                break

        print(f"[Facebook] Found {len(urls)} video URLs, fetching details...")

        # Map view counts to results (they appear in order)
        for i, views in enumerate(view_data):
            if i < len(results):
                results[i].views = views

        print(f"[Facebook] Found {len(results)} videos")

        # Fetch details for each video
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(results)))) as pool:
            futures = {
                pool.submit(self._enrich_video, result): result.video_url
                for result in results
            }
            completed = 0
            for future in as_completed(futures):
                video_url = futures[future]
                try:
                    future.result()
                    completed += 1
                    result = next((item for item in results if item.video_url == video_url), None)
                    if result and (result.title or result.author):
                        print(f"[Facebook] ({completed}/{len(results)}) {result.author} - {result.views} views")
                except Exception as e:
                    print(f"[Facebook] Error enriching {video_url}: {e}")

        return results

    def _enrich_video(self, result: VideoResult):
        response = self.fetch_page(
            result.video_url,
            wait_selector='meta[property="og:title"], meta[name="description"]',
            timeout=12000,
        )
        if response.status != 200:
            return

        meta = {}
        for tag in soup.find_all("meta"):
            prop = tag.get("property", "") or tag.get("name", "")
            content = tag.get("content", "")
            if prop and content:
                meta[prop] = content

        title = meta.get("og:title", "")[:100]
        description = meta.get("og:description", "")
        thumbnail = meta.get("og:image", "")

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
            num = float(number_str.replace(",", ""))
            multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix.upper(), 1)
            return int(num * multiplier)
        except ValueError:
            return None
