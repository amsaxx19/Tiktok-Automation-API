import re
from urllib.parse import quote
from bs4 import BeautifulSoup
from scraper.base import BaseScraper
from scraper.models import VideoResult


class FacebookScraper(BaseScraper):
    platform = "facebook"

    def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        print(f"[Facebook] Searching for: {keyword}")

        # Facebook requires login for most pages, use Google discovery
        encoded = quote(f"site:facebook.com/watch {keyword}")
        url = f"https://www.google.com/search?q={encoded}&num=30"

        resp = self.fetch_page(url)
        if resp.status_code != 200:
            print(f"[Facebook] Google search failed with status {resp.status_code}")
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

        results = []
        for i, video_url in enumerate(urls):
            try:
                result = self._scrape_video(video_url, keyword)
                if result:
                    results.append(result)
                    print(f"[Facebook] ({i+1}/{len(urls)}) {result.author} - {result.views} views")
            except Exception as e:
                print(f"[Facebook] Error scraping {video_url}: {e}")

        return results

    def _scrape_video(self, url: str, keyword: str) -> VideoResult | None:
        resp = self.fetch_page(url)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        meta = {}
        for tag in soup.find_all("meta"):
            prop = tag.get("property", "") or tag.get("name", "")
            content = tag.get("content", "")
            if prop and content:
                meta[prop] = content

        title = meta.get("og:title", "")[:100]
        description = meta.get("og:description", "")
        thumbnail = meta.get("og:image", "")

        author = ""
        url_match = re.search(r"facebook\.com/([^/]+)/", url)
        if url_match:
            author = url_match.group(1)

        # Try to extract view count from description
        views = None
        views_match = re.search(r"([\d,.]+)\s*([KMB])?\s*views?", description, re.IGNORECASE)
        if views_match:
            views = self._parse_abbreviated(views_match.group(1), views_match.group(2) or "")

        if not title and not description:
            # Still return basic result from URL
            return VideoResult(
                platform="facebook",
                keyword=keyword,
                video_url=url,
                author=author,
            )

        return VideoResult(
            platform="facebook",
            keyword=keyword,
            video_url=url,
            title=title,
            description=description,
            author=author,
            author_url=f"https://www.facebook.com/{author}" if author else "",
            views=views,
            thumbnail=thumbnail,
        )

    @staticmethod
    def _parse_abbreviated(number_str: str, suffix: str) -> int | None:
        try:
            num = float(number_str.replace(",", ""))
            multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix.upper(), 1)
            return int(num * multiplier)
        except ValueError:
            return None
