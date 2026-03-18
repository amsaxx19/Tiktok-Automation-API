import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.parse import quote
from scraper.base import BaseScraper
from scraper.models import VideoResult


class TikTokScraper(BaseScraper):
    platform = "tiktok"
    PROFILE_FETCH_TIMEOUT_MS = 15000
    VIDEO_FETCH_TIMEOUT_MS = 12000
    SEARCH_FETCH_TIMEOUT_MS = 16000

    @staticmethod
    def _parse_int(value) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_compact_int(value: str) -> int | None:
        if not value:
            return None
        cleaned = value.replace(",", "").strip()
        match = re.match(r"^(\d+(?:\.\d+)?)([KMB])?$", cleaned, re.IGNORECASE)
        if not match:
            return None
        number = float(match.group(1))
        multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(
            (match.group(2) or "").upper(),
            1,
        )
        return int(number * multiplier)

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

        response = self.fetch_page(
            url,
            wait_selector='a[href*="/video/"]',
            timeout=self.SEARCH_FETCH_TIMEOUT_MS,
        )
        if response.status != 200:
            print(f"[TikTok] Failed with status {response.status}")
            return []

        video_links = response.css('a[href*="/video/"]')
        urls = []
        seen = set()
        needs_extra_candidates = any(
            value is not None
            for value in (min_likes, max_likes, date_from, date_to)
        ) or sort in {"popular", "latest", "oldest"}
        target_count = max_results * 2 if needs_extra_candidates else max_results

        for link in video_links:
            href = link.attrib.get("href", "")
            if "/video/" in href and href not in seen:
                seen.add(href)
                urls.append(href)
            if len(urls) >= target_count:
                break

        print(f"[TikTok] Found {len(urls)} video URLs, fetching details...")

        results = []
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(urls)))) as pool:
            futures = {
                pool.submit(self._scrape_video, video_url, keyword): video_url
                for video_url in urls
            }
            for future in as_completed(futures):
                video_url = futures[future]
                try:
                    result = future.result()
                    if result and self._passes_filters(result, min_likes, max_likes, date_from, date_to):
                        results.append(result)
                        print(f"[TikTok] ({len(results)}/{max_results}) @{result.author} - {result.views} views")
                        if len(results) >= max_results:
                            break
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

        response = self.fetch_page(
            url,
            wait_selector='a[href*="/video/"]',
            timeout=self.PROFILE_FETCH_TIMEOUT_MS,
        )
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
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(urls)))) as pool:
            futures = {
                pool.submit(self._scrape_video, video_url, f"@{username}"): video_url
                for video_url in urls
            }
            for future in as_completed(futures):
                video_url = futures[future]
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                        print(f"[TikTok] ({len(results)}/{len(urls)}) {result.views} views")
                except Exception as e:
                    print(f"[TikTok] Error scraping {video_url}: {e}")

        if sort == "popular":
            results.sort(key=lambda r: r.views or 0, reverse=True)
        elif sort == "oldest":
            results.sort(key=lambda r: r.upload_date or "")

        return results

    def scrape_comments(self, video_url: str, max_comments: int = 50) -> list[dict]:
        print(f"[TikTok] Scraping comments from: {video_url}")
        api_comments = self._scrape_comments_via_api(video_url, max_comments)
        if api_comments:
            print(f"[TikTok] Extracted {len(api_comments)} comments via API")
            return api_comments

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

    def _scrape_comments_via_api(self, video_url: str, max_comments: int) -> list[dict]:
        comment_api_urls: list[str] = []
        extracted_comments: list[dict] = []

        def page_action(page):
            def on_response(resp):
                if "/api/comment/list/" in resp.url and resp.url not in comment_api_urls:
                    comment_api_urls.append(resp.url)

            page.on("response", on_response)
            for attempt in range(2):
                page.wait_for_timeout(3000)
                try:
                    page.get_by_text("Comments").click(timeout=2000, force=True)
                except Exception:
                    pass
                page.mouse.wheel(0, 2500)
                for _ in range(4):
                    if comment_api_urls:
                        break
                    page.wait_for_timeout(1500)
                if comment_api_urls:
                    break
                try:
                    page.reload(wait_until="load")
                except Exception:
                    pass

            if not comment_api_urls:
                return

            next_url = comment_api_urls[-1]
            seen_ids = set()

            while next_url and len(extracted_comments) < max_comments:
                payload = page.evaluate(
                    """async (commentUrl) => {
                      const resp = await fetch(commentUrl, { credentials: 'include' });
                      const text = await resp.text();
                      try {
                        return JSON.parse(text);
                      } catch (error) {
                        return { comments: [], has_more: false, cursor: null };
                      }
                    }""",
                    next_url,
                )

                for comment in payload.get("comments", []) or []:
                    cid = comment.get("cid") or comment.get("id")
                    if cid and cid in seen_ids:
                        continue
                    if cid:
                        seen_ids.add(cid)
                    extracted_comments.append({
                        "user": comment.get("user", {}).get("unique_id", "") or comment.get("user", {}).get("uniqueId", ""),
                        "nickname": comment.get("user", {}).get("nickname", ""),
                        "text": comment.get("text", ""),
                        "likes": self._parse_int(comment.get("digg_count") or comment.get("diggCount")) or 0,
                        "replies": self._parse_int(comment.get("reply_comment_total") or comment.get("replyCommentTotal")) or 0,
                        "create_time": comment.get("create_time") or comment.get("createTime", ""),
                    })
                    if len(extracted_comments) >= max_comments:
                        break

                if not payload.get("has_more") or len(extracted_comments) >= max_comments:
                    break

                cursor = payload.get("cursor")
                if cursor is None:
                    break
                next_url = self._update_comment_api_cursor(next_url, cursor)

        self.fetch_page(video_url, timeout=20000, wait=0, page_action=page_action)
        return extracted_comments[:max_comments]

    @staticmethod
    def _update_comment_api_cursor(url: str, cursor) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["cursor"] = [str(cursor)]
        query["count"] = [query.get("count", ["20"])[0]]
        new_query = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def get_video_comment_count(self, video_url: str) -> int | None:
        response = self.fetch_page(video_url, network_idle=True, timeout=20000)
        if response.status != 200:
            return None

        scripts = response.css('script#__UNIVERSAL_DATA_FOR_REHYDRATION__')
        if not scripts:
            return None

        try:
            data = json.loads(scripts[0].text)
        except json.JSONDecodeError:
            return None

        item = (
            data.get("__DEFAULT_SCOPE__", {})
            .get("webapp.video-detail", {})
            .get("itemInfo", {})
            .get("itemStruct", {})
        )
        return self._parse_int(item.get("stats", {}).get("commentCount"))

    def _scrape_video(self, url: str, keyword: str) -> VideoResult | None:
        response = self.fetch_page(
            url,
            wait_selector='script#__UNIVERSAL_DATA_FOR_REHYDRATION__',
            timeout=self.VIDEO_FETCH_TIMEOUT_MS,
        )
        if response.status != 200:
            return None

        scripts = response.css('script#__UNIVERSAL_DATA_FOR_REHYDRATION__')
        if not scripts:
            return self._scrape_video_meta_fallback(response, url, keyword)

        data = json.loads(scripts[0].text)
        video_detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
        item = video_detail.get("itemInfo", {}).get("itemStruct", {})
        if not item:
            return self._scrape_video_meta_fallback(response, url, keyword)

        stats = item.get("stats", {})
        author = item.get("author", {})
        video = item.get("video", {})
        music = item.get("music", {})

        caption = self._normalize_text(item.get("desc", ""))
        hashtags = re.findall(r"#(\w+)", caption)
        transcript = self._extract_transcript(item)

        return VideoResult(
            platform="tiktok",
            keyword=keyword,
            video_url=url,
            title=caption[:100],
            description=caption,
            caption=caption,
            author=author.get("uniqueId", ""),
            author_url=f"https://www.tiktok.com/@{author.get('uniqueId', '')}",
            views=self._parse_int(stats.get("playCount")),
            likes=self._parse_int(stats.get("diggCount")),
            comments=self._parse_int(stats.get("commentCount")),
            shares=self._parse_int(stats.get("shareCount")),
            saves=self._parse_int(stats.get("collectCount")),
            duration=self._parse_int(video.get("duration")),
            upload_date=item.get("createTime", ""),
            thumbnail=video.get("cover", ""),
            music=music.get("title", ""),
            transcript=transcript,
            transcript_source="spoken_text" if transcript else "",
            hashtags=hashtags,
        )

    def _scrape_video_meta_fallback(self, response, url: str, keyword: str) -> VideoResult | None:
        meta = {}
        for tag in response.css("meta"):
            prop = tag.attrib.get("property", "") or tag.attrib.get("name", "")
            content = tag.attrib.get("content", "")
            if prop and content:
                meta[prop] = content

        description = self._normalize_text(meta.get("og:description", "") or meta.get("description", ""))
        title = self._normalize_text(meta.get("og:title", "") or meta.get("twitter:title", ""))
        thumbnail = meta.get("og:image", "") or meta.get("twitter:image", "")

        author = ""
        url_match = re.search(r"tiktok\.com/@([^/]+)/video/", url)
        if url_match:
            author = url_match.group(1)

        likes = comments = None
        description_meta = meta.get("description", "")
        metrics_match = re.search(
            r"([\d.,KMB]+)\s+Likes?,\s+([\d.,KMB]+)\s+Comments?",
            description_meta,
            re.IGNORECASE,
        )
        if metrics_match:
            likes = self._parse_compact_int(metrics_match.group(1))
            comments = self._parse_compact_int(metrics_match.group(2))

        return VideoResult(
            platform="tiktok",
            keyword=keyword,
            video_url=url,
            title=description[:100] if description else title[:100],
            caption=description,
            description=description or title,
            author=author,
            author_url=f"https://www.tiktok.com/@{author}" if author else "",
            likes=likes,
            comments=comments,
            thumbnail=thumbnail,
            hashtags=re.findall(r"#(\w+)", description),
        )

    @classmethod
    def _extract_transcript(cls, item: dict) -> str:
        caption = cls._normalize_text(item.get("desc", ""))
        transcript_parts = []
        for content in item.get("contents", []) or []:
            desc = cls._normalize_text(content.get("desc", ""))
            if desc and desc not in transcript_parts:
                transcript_parts.append(desc)
        transcript = cls._normalize_text(" ".join(transcript_parts))
        if transcript and transcript.lower() != caption.lower():
            return transcript
        return ""

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"\s+", " ", (value or "")).strip()

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
