import json
import re
from urllib.parse import quote
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
        encoded = quote(keyword)
        url = f"https://www.tiktok.com/search?q={encoded}"

        response = self.fetch_page(url, network_idle=True, timeout=30000)
        if response.status != 200:
            print(f"[TikTok] Failed with status {response.status}")
            return []

        video_links = response.css('a[href*="/video/"]')
        urls = []
        seen = set()
        for link in video_links:
            href = link.attrib.get("href", "")
            if "/video/" in href and href not in seen:
                seen.add(href)
                urls.append(href)
            if len(urls) >= max_results * 2:  # Fetch extra for filtering
                break

        print(f"[TikTok] Found {len(urls)} video URLs, fetching details...")

        results = []
        for i, video_url in enumerate(urls):
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
        url = f"https://www.tiktok.com/@{username}"

        response = self.fetch_page(url, network_idle=True, timeout=30000)
        if response.status != 200:
            print(f"[TikTok] Failed to load profile with status {response.status}")
            return []

        video_links = response.css('a[href*="/video/"]')
        urls = []
        seen = set()
        for link in video_links:
            href = link.attrib.get("href", "")
            if "/video/" in href and href not in seen:
                seen.add(href)
                urls.append(href)
            if len(urls) >= max_results:
                break

        print(f"[TikTok] Found {len(urls)} videos on profile, fetching details...")

        results = []
        for i, video_url in enumerate(urls):
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
        response = self.fetch_page(video_url, network_idle=True, timeout=30000)
        if response.status != 200:
            return []

        scripts = response.css('script#__UNIVERSAL_DATA_FOR_REHYDRATION__')
        if not scripts:
            return []

        data = json.loads(scripts[0].text)
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

        # If comments weren't in hydration data, try DOM extraction
        if not comments:
            comment_els = response.css('div[class*="CommentItem"], div[data-e2e="comment-level-1"]')
            for el in comment_els[:max_comments]:
                text_els = el.css('p[data-e2e="comment-level-1"] span, span[data-e2e="comment-level-1"]')
                user_els = el.css('a[data-e2e="comment-username-1"], span[data-e2e="comment-username-1"]')
                text = text_els[0].text if text_els else ""
                user = user_els[0].text if user_els else ""
                if text:
                    comments.append({"user": user, "text": text, "likes": 0, "replies": 0})

        print(f"[TikTok] Extracted {len(comments)} comments")
        return comments

    def _scrape_video(self, url: str, keyword: str) -> VideoResult | None:
        response = self.fetch_page(url)
        if response.status != 200:
            return None

        scripts = response.css('script#__UNIVERSAL_DATA_FOR_REHYDRATION__')
        if not scripts:
            return None

        data = json.loads(scripts[0].text)
        video_detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
        item = video_detail.get("itemInfo", {}).get("itemStruct", {})
        if not item:
            return None

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
