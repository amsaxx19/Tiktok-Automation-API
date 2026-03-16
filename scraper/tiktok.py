import json
import re
from urllib.parse import quote
from bs4 import BeautifulSoup
from scraper.base import BaseScraper
from scraper.models import VideoResult


class TikTokScraper(BaseScraper):
    platform = "tiktok"

    def search(
        self,
        keyword: str,
        max_results: int = 20,
        sort: str = "relevance",
        min_likes: int | None = None,
        max_likes: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[VideoResult]:
        print(f"[TikTok] Searching for: {keyword}")

        # TikTok search page requires JS, use Google as discovery
        encoded = quote(f"site:tiktok.com/*/video {keyword}")
        url = f"https://www.google.com/search?q={encoded}&num=30"

        resp = self.fetch_page(url)
        if resp.status_code != 200:
            print(f"[TikTok] Google search failed with status {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        urls = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "tiktok.com" in href and "/video/" in href:
                if "url?q=" in href:
                    href = href.split("url?q=")[1].split("&")[0]
                href = href.split("#")[0]
                vid_match = re.search(r"/video/(\d+)", href)
                if vid_match and vid_match.group(1) not in seen:
                    seen.add(vid_match.group(1))
                    urls.append(href)
            if len(urls) >= max_results * 2:
                break

        print(f"[TikTok] Found {len(urls)} video URLs, fetching details...")

        results = []
        for video_url in urls:
            if len(results) >= max_results:
                break
            try:
                result = self._scrape_video(video_url, keyword)
                if result and self._passes_filters(result, min_likes, max_likes, date_from, date_to):
                    results.append(result)
                    print(f"[TikTok] ({len(results)}/{max_results}) @{result.author} - {result.views} views")
            except Exception as e:
                print(f"[TikTok] Error scraping {video_url}: {e}")

        if sort == "popular":
            results.sort(key=lambda r: r.views or 0, reverse=True)
        elif sort == "latest":
            results.sort(key=lambda r: r.upload_date or "", reverse=True)
        elif sort == "oldest":
            results.sort(key=lambda r: r.upload_date or "")

        return results

    def scrape_profile(
        self,
        username: str,
        max_results: int = 30,
        sort: str = "latest",
    ) -> list[VideoResult]:
        username = username.lstrip("@")
        print(f"[TikTok] Scraping profile: @{username}")

        # Use Google to discover profile videos
        encoded = quote(f"site:tiktok.com/@{username}/video")
        url = f"https://www.google.com/search?q={encoded}&num=30"

        resp = self.fetch_page(url)
        if resp.status_code != 200:
            print(f"[TikTok] Google search failed with status {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        urls = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "tiktok.com" in href and "/video/" in href:
                if "url?q=" in href:
                    href = href.split("url?q=")[1].split("&")[0]
                href = href.split("#")[0]
                vid_match = re.search(r"/video/(\d+)", href)
                if vid_match and vid_match.group(1) not in seen:
                    seen.add(vid_match.group(1))
                    urls.append(href)
            if len(urls) >= max_results:
                break

        print(f"[TikTok] Found {len(urls)} videos, fetching details...")

        results = []
        for video_url in urls:
            try:
                result = self._scrape_video(video_url, f"@{username}")
                if result:
                    results.append(result)
                    print(f"[TikTok] ({len(results)}/{len(urls)}) {result.views} views")
            except Exception as e:
                print(f"[TikTok] Error: {e}")

        if sort == "popular":
            results.sort(key=lambda r: r.views or 0, reverse=True)
        elif sort == "oldest":
            results.sort(key=lambda r: r.upload_date or "")

        return results

    def scrape_comments(self, video_url: str, max_comments: int = 50) -> list[dict]:
        print(f"[TikTok] Scraping comments from: {video_url}")
        resp = self.fetch_page(video_url)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        script = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
        if not script or not script.string:
            return []

        data = json.loads(script.string)
        comment_data = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
        comments_list = comment_data.get("commentList", [])

        comments = []
        for c in comments_list[:max_comments]:
            comments.append({
                "user": c.get("user", {}).get("uniqueId", ""),
                "nickname": c.get("user", {}).get("nickname", ""),
                "text": c.get("text", ""),
                "likes": c.get("diggCount", 0),
                "replies": c.get("replyCommentTotal", 0),
                "create_time": c.get("createTime", ""),
            })

        print(f"[TikTok] Extracted {len(comments)} comments")
        return comments

    def _scrape_video(self, url: str, keyword: str) -> VideoResult | None:
        resp = self.fetch_page(url)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # Try hydration data first
        script = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
        if script and script.string:
            try:
                data = json.loads(script.string)
                video_detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
                item = video_detail.get("itemInfo", {}).get("itemStruct", {})
                if item:
                    stats = item.get("stats", {})
                    author = item.get("author", {})
                    video = item.get("video", {})
                    music = item.get("music", {})
                    hashtags = re.findall(r"#(\w+)", item.get("desc", ""))

                    return VideoResult(
                        platform="tiktok",
                        keyword=keyword,
                        video_url=url,
                        title=item.get("desc", "")[:100],
                        description=item.get("desc", ""),
                        author=author.get("uniqueId", ""),
                        author_url=f"https://www.tiktok.com/@{author.get('uniqueId', '')}",
                        views=stats.get("playCount"),
                        likes=stats.get("diggCount"),
                        comments=stats.get("commentCount"),
                        shares=stats.get("shareCount"),
                        saves=stats.get("collectCount"),
                        duration=video.get("duration"),
                        upload_date=item.get("createTime", ""),
                        thumbnail=video.get("cover", ""),
                        music=music.get("title", ""),
                        hashtags=hashtags,
                    )
            except (json.JSONDecodeError, KeyError):
                pass

        # Fallback: extract from meta tags
        meta = {}
        for tag in soup.find_all("meta"):
            prop = tag.get("property", "") or tag.get("name", "")
            content = tag.get("content", "")
            if prop and content:
                meta[prop] = content

        title = meta.get("og:title", "") or meta.get("twitter:title", "")
        description = meta.get("og:description", "") or meta.get("description", "")
        thumbnail = meta.get("og:image", "")

        # Try to extract author from URL
        author_match = re.search(r"tiktok\.com/@([^/]+)", url)
        author = author_match.group(1) if author_match else ""

        if not title and not description:
            return None

        return VideoResult(
            platform="tiktok",
            keyword=keyword,
            video_url=url,
            title=title[:100],
            description=description,
            author=author,
            author_url=f"https://www.tiktok.com/@{author}" if author else "",
            thumbnail=thumbnail,
        )

    @staticmethod
    def _passes_filters(result, min_likes, max_likes, date_from, date_to) -> bool:
        if min_likes is not None and (result.likes or 0) < min_likes:
            return False
        if max_likes is not None and (result.likes or 0) > max_likes:
            return False
        if date_from and result.upload_date:
            try:
                if int(result.upload_date) < int(date_from):
                    return False
            except ValueError:
                pass
        if date_to and result.upload_date:
            try:
                if int(result.upload_date) > int(date_to):
                    return False
            except ValueError:
                pass
        return True
