import json
import re
from urllib.parse import quote
from scraper.base import BaseScraper
from scraper.models import VideoResult
from scrapling.fetchers import AsyncStealthySession
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
    _YT_TRANSCRIPT_AVAILABLE = True
except ImportError:
    _YT_TRANSCRIPT_AVAILABLE = False


class YouTubeScraper(BaseScraper):
    platform = "youtube"

    async def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        print(f"[YouTube] Searching for: {keyword}")
        encoded = quote(keyword)
        url = f"https://www.youtube.com/results?search_query={encoded}"

        async with AsyncStealthySession(headless=True) as session:
            try:
                response = await session.fetch(url, timeout=12000)
            except Exception as exc:
                print(f"[YouTube] Native search failed: {exc}")
                return await self._fallback_search(keyword, max_results)

            if response.status != 200:
                print(f"[YouTube] Failed with status {response.status}")
                return await self._fallback_search(keyword, max_results)

            page_text = response.text or ""

            # Also try extracting from script elements directly
            script_texts = []
            for script in response.css("script"):
                text = script.text or ""
                if "ytInitialData" in text:
                    script_texts.append(text)

        # Extract ytInitialData from page text or script elements
        yt_data = None
        sources = [page_text] + script_texts
        for source in sources:
            for pattern in [
                r"var ytInitialData\s*=\s*(\{.*?\});\s*(?:</script>|$)",
                r"window\[\"ytInitialData\"\]\s*=\s*(\{.*?\});\s*",
                r"ytInitialData\s*=\s*(\{.+\})\s*;",
            ]:
                match = re.search(pattern, source, re.DOTALL)
                if match:
                    try:
                        yt_data = json.loads(match.group(1))
                        break
                    except json.JSONDecodeError:
                        continue
            if yt_data:
                break

        if not yt_data:
            print(f"[YouTube] Could not find ytInitialData (page_text length: {len(page_text)}, scripts with ytInitialData: {len(script_texts)})")
            return await self._fallback_search(keyword, max_results)

        results = []
        contents = (
            yt_data.get("contents", {})
            .get("twoColumnSearchResultsRenderer", {})
            .get("primaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )

        for section in contents:
            items = section.get("itemSectionRenderer", {}).get("contents", [])
            for item in items:
                video = item.get("videoRenderer", {})
                if not video:
                    continue

                vid_id = video.get("videoId", "")
                if not vid_id:
                    continue

                title_runs = video.get("title", {}).get("runs", [])
                title = title_runs[0].get("text", "") if title_runs else ""

                channel_runs = video.get("ownerText", {}).get("runs", [])
                channel = channel_runs[0].get("text", "") if channel_runs else ""
                channel_url = ""
                if channel_runs:
                    nav = channel_runs[0].get("navigationEndpoint", {})
                    channel_url = "https://youtube.com" + nav.get("commandMetadata", {}).get(
                        "webCommandMetadata", {}
                    ).get("url", "")

                views_text = video.get("viewCountText", {}).get("simpleText", "")
                views = self._parse_count(views_text)

                length_text = video.get("lengthText", {}).get("simpleText", "")
                duration = self._parse_duration(length_text)

                published = video.get("publishedTimeText", {}).get("simpleText", "")

                desc_runs = video.get("detailedMetadataSnippets", [{}])
                desc = ""
                if desc_runs:
                    snippet_runs = desc_runs[0].get("snippetText", {}).get("runs", [])
                    desc = "".join(r.get("text", "") for r in snippet_runs)

                thumbnail = ""
                thumbs = video.get("thumbnail", {}).get("thumbnails", [])
                if thumbs:
                    thumbnail = thumbs[-1].get("url", "")

                transcript, transcript_source = YouTubeScraper._get_transcript(vid_id) if _YT_TRANSCRIPT_AVAILABLE else ("", "")
                results.append(
                    VideoResult(
                        platform="youtube",
                        keyword=keyword,
                        video_url=f"https://youtube.com/watch?v={vid_id}",
                        title=title,
                        description=desc,
                        author=channel,
                        author_url=channel_url,
                        views=views,
                        duration=duration,
                        upload_date=published,
                        thumbnail=thumbnail,
                        transcript=transcript,
                        transcript_source=transcript_source,
                    )
                )

                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

        if len(results) < max_results:
            fallback_results = await self._fallback_search(keyword, max_results - len(results))
            seen_urls = {item.video_url for item in results}
            for item in fallback_results:
                if item.video_url not in seen_urls:
                    results.append(item)
                    seen_urls.add(item.video_url)
                if len(results) >= max_results:
                    break

        print(f"[YouTube] Found {len(results)} videos")
        return results

    async def _fallback_search(self, keyword: str, max_results: int) -> list[VideoResult]:
        if max_results <= 0:
            return []
        print(f"[YouTube] Falling back to Google discovery for: {keyword}")
        async with AsyncStealthySession(headless=True) as session:
            urls = await self._google_discover_urls(
                session,
                f'site:youtube.com ("/watch" OR "/shorts/") {keyword}',
                ["youtube.com"],
                ["/watch", "/shorts/"],
                max_results=max_results,
            )
            if not urls:
                return [
                    self._make_placeholder_result(
                        keyword,
                        f"https://www.youtube.com/results?search_query={quote(keyword)}",
                        title=f"YouTube: {keyword}",
                        description=f"Hasil pencarian YouTube untuk '{keyword}'.",
                    )
                ]

            tasks = [self._scrape_video_page(session, url, keyword) for url in urls[:max_results]]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
            results = []
            for index, item in enumerate(raw_results):
                if isinstance(item, Exception) or not item:
                    results.append(
                        self._make_placeholder_result(
                            keyword,
                            urls[index],
                            title=f"Video YouTube: {keyword}",
                        )
                    )
                else:
                    results.append(item)
            return results[:max_results]

    async def _scrape_video_page(self, session: AsyncStealthySession, url: str, keyword: str) -> VideoResult | None:
        response = await session.fetch(
            url,
            wait_selector='meta[property="og:title"], meta[name="description"]',
            timeout=12000,
        )
        if response.status != 200:
            return None

        meta = {}
        for tag in response.css("meta"):
            prop = tag.attrib.get("property", "") or tag.attrib.get("name", "")
            content = tag.attrib.get("content", "")
            if prop and content:
                meta[prop] = content

        title = meta.get("og:title", "") or meta.get("twitter:title", "")
        description = meta.get("og:description", "") or meta.get("description", "")
        thumbnail = meta.get("og:image", "") or meta.get("twitter:image", "")
        channel = meta.get("og:video:tag", "")
        match = re.search(r"(?:watch\?v=|/shorts/)([A-Za-z0-9_-]{6,})", url)
        transcript = transcript_source = ""
        if match and _YT_TRANSCRIPT_AVAILABLE:
            transcript, transcript_source = YouTubeScraper._get_transcript(match.group(1))

        return VideoResult(
            platform="youtube",
            keyword=keyword,
            video_url=url,
            title=(title or description or keyword)[:100],
            description=description,
            author=channel,
            author_url="",
            thumbnail=thumbnail,
            transcript=transcript,
            transcript_source=transcript_source or "",
        )

    @staticmethod
    def _get_transcript(vid_id: str) -> tuple[str, str]:
        """Fetch YouTube transcript/captions. Returns (transcript_text, source)."""
        if not _YT_TRANSCRIPT_AVAILABLE or not vid_id:
            return "", ""
        try:
            api = YouTubeTranscriptApi()
            pref_langs = ["id", "en", "en-US", "en-GB"]
            transcript_list = api.list(vid_id)
            transcript = None
            source = ""
            # Try manual captions first (higher quality)
            try:
                transcript = transcript_list.find_manually_created_transcript(pref_langs)
                source = "manual_caption"
            except NoTranscriptFound:
                pass
            # Fall back to auto-generated
            if not transcript:
                try:
                    transcript = transcript_list.find_generated_transcript(pref_langs)
                    source = "auto_caption"
                except NoTranscriptFound:
                    pass
            # Fall back to any available transcript
            if not transcript:
                for t in transcript_list:
                    transcript = t
                    source = "auto_caption" if t.is_generated else "manual_caption"
                    break
            if transcript:
                entries = transcript.fetch()
                text = " ".join(e.text.strip() for e in entries if e.text.strip())
                text = re.sub(r"\s+", " ", text).strip()
                return text, source
        except (TranscriptsDisabled, Exception):
            pass
        return "", ""

    @staticmethod
    def _parse_count(text: str) -> int | None:
        if not text:
            return None
        text = text.lower().replace(",", "").replace(" views", "").replace(" view", "").strip()
        match = re.match(r"^(\d+(?:\.\d+)?)([kmb])?$", text)
        if match:
            value = float(match.group(1))
            multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(match.group(2), 1)
            return int(value * multiplier)
        try:
            return int(text)
        except ValueError:
            return None

    @staticmethod
    def _parse_duration(text: str) -> int | None:
        if not text:
            return None
        parts = text.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return int(parts[0])
        except ValueError:
            return None
