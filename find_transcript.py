import asyncio
import re
from scrapling.fetchers import AsyncStealthySession

async def main():
    async with AsyncStealthySession(headless=True) as session:
        # A video known to have someone speaking, so it should have auto-captions
        url = "https://www.tiktok.com/@streetorvintage/video/7376307130833636616"
        print(f"Fetching {url}")
        resp = await session.fetch(url, network_idle=True, timeout=20000)
        
        html = resp.text
        print(f"Total HTML length: {len(html)}")
        
        # Look for common subtitle JSON keys anywhere in the HTML
        patterns = [
            r'"subtitleInfos":',
            r'"closedCaptions":',
            r'"subtitles":',
            r'"transcript":',
            r'"words":\s*\[',
            r'quitting a job', # A phrase known to be spoken in the video
        ]
        
        for p in patterns:
            matches = re.finditer(p, html, re.IGNORECASE)
            count = sum(1 for _ in matches)
            print(f"Pattern '{p}' found {count} times")
            
        with open("tiktok_raw.html", "w") as f:
            f.write(html)
        print("Detailed HTML dumped to tiktok_raw.html")

if __name__ == "__main__":
    asyncio.run(main())
