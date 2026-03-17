import json
import re
from urllib.parse import quote
from bs4 import BeautifulSoup
from scraper.base import BaseScraper
from scraper.models import VideoResult


class InstagramScraper(BaseScraper):
    platform = "instagram"

    def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        print(f"[Instagram] Searching for: {keyword}")

        # Instagram requires login for direct access, use Google discovery
        encoded = quote(f"site:instagram.com/reel {keyword}")
        url = f"https://www.google.com/search?q={encoded}&num=30"

        resp = self.fetch_page(url)
        if resp.status_code != 200:
            print(f"[Instagram] Google search failed with status {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        urls = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "instagram.com" in href and ("/reel/" in href or "/p/" in href):
                if "url?q=" in href:
                    href = href.split("url?q=")[1].split("&")[0]
                href = href.split("#")[0]
                if href not in seen:
                    seen.add(href)
                    urls.append(href)
            if len(urls) >= max_results:
                break

        print(f"[Instagram] Found {len(urls)} posts/reels, fetching details...")

        results = []
        for i, post_url in enumerate(urls):
            try:
                result = self._scrape_post(post_url, keyword)
                if result:
                    results.append(result)
                    print(f"[Instagram] ({i+1}/{len(urls)}) @{result.author} - {result.likes} likes")
            except Exception as e:
                print(f"[Instagram] Error scraping {post_url}: {e}")

        return results

    @staticmethod
    def _parse_abbreviated(number_str: str, suffix: str | None) -> int | None:
        try:
            num = float(number_str.replace(",", ""))
            multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get((suffix or "").upper(), 1)
            return int(num * multiplier)
        except ValueError:
            return None

    def _scrape_post(self, url: str, keyword: str) -> VideoResult | None:
        resp = self.fetch_page(url)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # Extract data from meta tags
        meta = {}
        for tag in soup.find_all("meta"):
            prop = tag.get("property", "") or tag.get("name", "")
            content = tag.get("content", "")
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
