"""
Test: can we extract TikTok video download URL from the page meta tags?
If og:video or similar meta tag contains a direct video URL,
we can download it and transcribe it with Whisper.
"""
import asyncio
import json
from scrapling.fetchers import AsyncStealthySession

async def main():
    async with AsyncStealthySession(headless=True) as session:
        url = "https://www.tiktok.com/@officialinews/video/7619550440434273553"
        print(f"Fetching {url}")
        resp = await session.fetch(url, network_idle=True, timeout=20000)
        
        # Extract ALL meta tags
        meta = {}
        for tag in resp.css("meta"):
            prop = tag.attrib.get("property", "") or tag.attrib.get("name", "")
            content = tag.attrib.get("content", "")
            if prop and content:
                meta[prop] = content
        
        print("\n--- All meta tags ---")
        for k, v in sorted(meta.items()):
            if any(x in k.lower() for x in ["video", "url", "image", "title", "description"]):
                print(f"  {k}: {v[:200]}")

        # Also check for video source elements
        video_els = resp.css("video source, video")
        print(f"\n--- Video elements: {len(video_els)} ---")
        for el in video_els:
            src = el.attrib.get("src", "")
            if src:
                print(f"  src: {src[:200]}")

if __name__ == "__main__":
    asyncio.run(main())
