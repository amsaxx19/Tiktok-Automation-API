import asyncio
from scrapling.fetchers import AsyncStealthySession

async def main():
    async with AsyncStealthySession(headless=False) as session:
        url = "https://www.tiktok.com/@streetorvintage/video/7376307130833636616"
        print(f"Fetching {url} with Scrapling...")
        
        # We need access to the Playwright page to click things and get active DOM
        # Scrapling fetch() exposes the selector. But to interact, we use page_action
        subtitle_text = []

        async def page_action(page):
            # Wait for network idle and let the video play
            await page.wait_for_timeout(5000)
            
            # Try to click play if paused
            try:
                play_btn = page.locator('[data-e2e="video-play-icon"]')
                if await play_btn.is_visible():
                    await play_btn.click()
            except Exception as e:
                pass
                
            # Try to click the CC (Closed Captions) button if it exists
            try:
                cc_btn = page.locator('div[class*="CaptionIcon"], [data-e2e="caption-icon"]')
                if await cc_btn.count() > 0:
                    await cc_btn.first.click()
            except Exception as e:
                pass
                
            # Wait for some subtitles to show up on screen
            for i in range(15):
                await page.wait_for_timeout(1000)
                # Subtitle container usually changes text continuously as video plays
                els = await page.locator('div[class*="Subtitle"], div[data-e2e="video-subtitle"], div[class*="CaptionText"]').all_inner_texts()
                for el in els:
                    if el and el not in subtitle_text:
                        subtitle_text.append(el)
                        print(f"Captured subtitle line: {el}")
            
            # Also check if there's any JSON we missed
            html = await page.content()
            if "quitting a job" in html.lower():
                print("Found transcript text hidden in HTML!")
            else:
                print("No transcript text found in raw HTML.")

        await session.fetch(url, timeout=30000, wait=0, page_action=page_action)
        
        print("\nFinal captured DOM subtitles:", " ".join(subtitle_text))

if __name__ == "__main__":
    asyncio.run(main())
