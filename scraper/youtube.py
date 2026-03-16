import json
import re
from urllib.parse import quote
from bs4 import BeautifulSoup
from scraper.base import BaseScraper
from scraper.models import VideoResult


class YouTubeScraper(BaseScraper):
    platform = "youtube"

    def search(self, keyword: str, max_results: int = 20) -> list[VideoResult]:
        print(f"[YouTube] Searching for: {keyword}")
        encoded = quote(keyword)
        url = f"https://www.youtube.com/results?search_query={encoded}"

        resp = self.fetch_page(url)
        if resp.status_code != 200:
            print(f"[YouTube] Failed with status {resp.status_code}")
            return []

        # Extract ytInitialData from script tags
        yt_data = None
        match = re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", resp.text, re.DOTALL)
        if match:
            try:
                yt_data = json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        if not yt_data:
            print("[YouTube] Could not find ytInitialData")
            return []

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
                    )
                )

                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

        print(f"[YouTube] Found {len(results)} videos")
        return results

    @staticmethod
    def _parse_count(text: str) -> int | None:
        if not text:
            return None
        text = text.lower().replace(",", "").replace(" views", "").replace(" view", "").strip()
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
