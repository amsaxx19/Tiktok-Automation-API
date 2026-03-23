import asyncio
from playwright.async_api import async_playwright
import re

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        url = "https://www.tiktok.com/@streetorvintage/video/7376307130833636616"
        print(f"Fetching {url}")
        
        # Go to page and wait for network to settle
        await page.goto(url, wait_until="networkidle", timeout=20000)
        
        # Get full rendered HTML
        html = await page.content()
        print(f"Total HTML length: {len(html)}")
        
        patterns = [
            r'"subtitleInfos":',
            r'"closedCaptions":',
            r'"subtitles":',
            r'"transcript":',
            r'"words":\s*\[',
            r'quitting a job',
        ]
        
        for p in patterns:
            matches = list(re.finditer(p, html, re.IGNORECASE))
            print(f"Pattern '{p}' found {len(matches)} times")
            
        with open("tiktok_playwright_raw.html", "w") as f:
            f.write(html)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
