import json
import re
import asyncio
from urllib.parse import quote
from scrapling.fetchers import AsyncStealthySession
from scraper.base import BaseScraper
from scraper.models import VideoResult


class InstagramScraper(BaseScraper):
    platform = "instagram"

    async def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        print(f"[Instagram] Searching for: {keyword}")

        async with AsyncStealthySession(headless=True) as session:
            urls = await self._search_via_hashtag(session, keyword, max_results)

            # Fallback to Google if hashtag page is login-walled or empty
            if not urls:
                print("[Instagram] Hashtag page blocked, falling back to Google discovery...")
                urls = await self._search_via_google(session, keyword, max_results)

            print(f"[Instagram] Found {len(urls)} posts/reels, fetching details concurrently...")

            results = []
            tasks = [self._scrape_post(session, post_url, keyword) for post_url in urls]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in raw_results:
                if isinstance(result, Exception):
                    print(f"[Instagram] Error scraping video: {result}")
                    continue
                if result:
                    results.append(result)
                    print(f"[Instagram] ({len(results)}/{len(urls)}) @{result.author} - {result.likes} likes")
                    if len(results) >= max_results:
                        break

        return results

    async def _search_via_hashtag(self, session: AsyncStealthySession, keyword: str, max_results: int) -> list[str]:
        tag = keyword.replace(" ", "").lower()
        url = f"https://www.instagram.com/explore/tags/{quote(tag)}/"

        response = await session.fetch(
            url,
            wait_selector='a[href*="/reel/"], a[href*="/p/"]',
            timeout=25000,
        )
        if response.status != 200:
            return []

        reel_links = response.css('a[href*="/reel/"]')
        post_links = response.css('a[href*="/p/"]')

        urls = []
        seen = set()
        for link in list(reel_links) + list(post_links):
            href = link.attrib.get("href", "")
            if href and href not in seen:
                seen.add(href)
                full_url = f"https://www.instagram.com{href}" if href.startswith("/") else href
                urls.append(full_url)
            if len(urls) >= max_results:
                break
        return urls

    async def _search_via_google(self, session: AsyncStealthySession, keyword: str, max_results: int) -> list[str]:
        encoded = quote(f"site:instagram.com/reel {keyword}")
        url = f"https://www.google.com/search?q={encoded}&num=30"

        response = await session.fetch(url, network_idle=True, timeout=12000)
        if response.status != 200:
            return []

        urls = []
        seen = set()
        for link in response.css("a"):
            href = link.attrib.get("href", "")
            if "instagram.com" in href and ("/reel/" in href or "/p/" in href):
                if "url?q=" in href:
                    href = href.split("url?q=")[1].split("&")[0]
                href = href.split("#")[0]
                if href not in seen:
                    seen.add(href)
                    urls.append(href)
            if len(urls) >= max_results:
                break
        return urls

    @staticmethod
    def _parse_abbreviated(number_str: str, suffix: str | None) -> int | None:
        try:
            num = float(number_str.replace(",", ""))
            multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get((suffix or "").upper(), 1)
            return int(num * multiplier)
        except ValueError:
            return None

    async def _scrape_post(self, session: AsyncStealthySession, url: str, keyword: str) -> VideoResult | None:
        response = await session.fetch(url, wait_selector='meta[property="og:title"], meta[name="description"]', timeout=12000)
        if response.status != 200:
            return None

        # Extract data from meta tags using Scrapling's CSS selectors
        meta = {}
        for tag in response.css("meta"):
            prop = tag.attrib.get("property", "") or tag.attrib.get("name", "")
            content = tag.attrib.get("content", "")
            if prop and content:
                meta[prop] = content

        description = meta.get("og:description", "") or meta.get("description", "")
        title = meta.get("og:title", "") or meta.get("twitter:title", "")
        thumbnail = meta.get("og:image", "") or meta.get("twitter:image", "")
        canonical_url = meta.get("og:url", url)

        likes = None
        comments = None
        author = ""
        caption = ""

        likes_match = re.search(r"([\d,.]+)\s*([KMB])?\s*likes?", description, re.IGNORECASE)
        if likes_match:
            likes = self._parse_abbreviated(likes_match.group(1), likes_match.group(2))

        comments_match = re.search(r"([\d,.]+)\s*([KMB])?\s*comments?", description, re.IGNORECASE)
        if comments_match:
            comments = self._parse_abbreviated(comments_match.group(1), comments_match.group(2))

        author_match = re.search(r"[-\u2013]\s*(\w+)\s+on\s+", description)
        if author_match:
            author = author_match.group(1)

        if " on Instagram:" in title:
            parts = title.split(" on Instagram:", 1)
            if len(parts) == 2:
                if not author:
                    author = parts[0].split("(")[-1].split(")")[0] if "(" in parts[0] else parts[0].strip()
                caption = parts[1].strip().strip('"').strip("\u201c").strip("\u201d")

        hashtags = re.findall(r"#(\w+)", description + caption)

        ig_keywords = meta.get("keywords", "")
        if ig_keywords and not hashtags:
            hashtags = [k.strip() for k in ig_keywords.split(",") if k.strip()]

        return VideoResult(
            platform="instagram",
            keyword=keyword,
            video_url=canonical_url or url,
            title=caption[:100] if caption else title[:100],
            description=caption or description,
            author=author,
            author_url=f"https://www.instagram.com/{author}/" if author else "",
            views=None,
            likes=likes,
            comments=comments,
            thumbnail=thumbnail,
            hashtags=hashtags,
        )
