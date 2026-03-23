import re
import asyncio
from urllib.parse import quote
import httpx
from scrapling.fetchers import AsyncStealthySession
from scraper.base import BaseScraper
from scraper.models import VideoResult


class TwitterScraper(BaseScraper):
    platform = "twitter"

    async def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        print(f"[Twitter/X] Searching for: {keyword}")
        print("[Twitter/X] Note: X requires login for direct search, using Google as discovery method")

        encoded = quote(f"site:x.com {keyword}")
        url = f"https://www.google.com/search?q={encoded}&num=30"

        async with AsyncStealthySession(headless=True) as session:
            response = await session.fetch(url, network_idle=True, timeout=20000)
            if response.status != 200:
                print(f"[Twitter/X] Google search failed with status {response.status}")
                return []

            # Extract x.com/twitter.com status links from Google results
            all_links = response.css("a")
            urls = []
            seen_ids = set()
            for link in all_links:
                href = link.attrib.get("href", "")
                if "/status/" in href and ("x.com" in href or "twitter.com" in href):
                    # Clean Google redirect URLs
                    if "url?q=" in href:
                        href = href.split("url?q=")[1].split("&")[0]
                    # Extract tweet ID to deduplicate
                    match = re.search(r"/status/(\d+)", href)
                    if match and match.group(1) not in seen_ids:
                        seen_ids.add(match.group(1))
                        # Normalize URL (remove fragments)
                        clean_url = href.split("#")[0]
                        urls.append(clean_url)
                if len(urls) >= max_results:
                    break

        print(f"[Twitter/X] Found {len(urls)} tweet URLs, fetching details concurrently...")

        results = []
        async with httpx.AsyncClient() as client:
            tasks = [self._scrape_tweet(client, tweet_url, keyword) for tweet_url in urls]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, result in enumerate(raw_results):
                if isinstance(result, Exception):
                    print(f"[Twitter/X] Error scraping {urls[i]}: {result}")
                    continue
                if result:
                    results.append(result)
                    print(f"[Twitter/X] ({len(results)}/{len(urls)}) @{result.author}")

        return results

    async def _scrape_tweet(self, client: httpx.AsyncClient, url: str, keyword: str) -> VideoResult | None:
        match = re.search(r"(?:x\.com|twitter\.com)/(\w+)/status/(\d+)", url)
        if not match:
            return None

        username = match.group(1)
        author = username

        # Use oembed API (returns JSON, no browser needed)
        oembed_url = f"https://publish.twitter.com/oembed?url={url}"
        description = ""
        try:
            resp = await client.get(oembed_url, timeout=10, follow_redirects=True)
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
            author_url=f"https://x.com/{username}",
        )
