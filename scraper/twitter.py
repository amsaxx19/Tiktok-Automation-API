import re
from urllib.parse import quote
import httpx
from bs4 import BeautifulSoup
from scraper.base import BaseScraper
from scraper.models import VideoResult


class TwitterScraper(BaseScraper):
    platform = "twitter"

    def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        print(f"[Twitter/X] Searching for: {keyword}")
        print("[Twitter/X] Note: X requires login for direct search, using Google as discovery method")

        encoded = quote(f"site:x.com {keyword}")
        url = f"https://www.google.com/search?q={encoded}&num=30"

        resp = self.fetch_page(url)
        if resp.status_code != 200:
            print(f"[Twitter/X] Google search failed with status {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        urls = []
        seen_ids = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/status/" in href and ("x.com" in href or "twitter.com" in href):
                if "url?q=" in href:
                    href = href.split("url?q=")[1].split("&")[0]
                match = re.search(r"/status/(\d+)", href)
                if match and match.group(1) not in seen_ids:
                    seen_ids.add(match.group(1))
                    clean_url = href.split("#")[0]
                    urls.append(clean_url)
            if len(urls) >= max_results:
                break

        print(f"[Twitter/X] Found {len(urls)} tweet URLs, fetching details...")

        results = []
        for i, tweet_url in enumerate(urls):
            try:
                result = self._scrape_tweet(tweet_url, keyword)
                if result:
                    results.append(result)
                    print(f"[Twitter/X] ({i+1}/{len(urls)}) @{result.author}")
            except Exception as e:
                print(f"[Twitter/X] Error scraping {tweet_url}: {e}")

        return results

    def _scrape_tweet(self, url: str, keyword: str) -> VideoResult | None:
        match = re.search(r"(?:x\.com|twitter\.com)/(\w+)/status/(\d+)", url)
        if not match:
            return None

        author = match.group(1)

        # Use oembed API (returns JSON, no browser needed)
        oembed_url = f"https://publish.twitter.com/oembed?url={url}"
        description = ""
        try:
            resp = httpx.get(oembed_url, timeout=10, follow_redirects=True)
            if resp.status_code == 200:
                data = resp.json()
                html = data.get("html", "")
                description = re.sub(r"<[^>]+>", " ", html).strip()
                description = re.sub(r"\s+", " ", description)
                author = data.get("author_name", author)
        except Exception:
            pass

        return VideoResult(
            platform="twitter",
            keyword=keyword,
            video_url=url,
            title=description[:100] if description else "",
            description=description,
            author=author,
            author_url=f"https://x.com/{author}",
        )
