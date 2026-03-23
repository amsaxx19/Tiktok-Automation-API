"""
Use Playwright route interception to capture TikTok video bytes,
then transcribe with Whisper. Fully automated.
"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    video_path = "/tmp/tiktok_test_video.mp4"
    captured = {"done": False}
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # Intercept video requests via route
        async def handle_route(route):
            if captured["done"]:
                await route.continue_()
                return
            response = await route.fetch()
            body = await response.body()
            if len(body) > 50000:
                with open(video_path, "wb") as f:
                    f.write(body)
                print(f"  ✅ Captured video: {len(body)} bytes")
                captured["done"] = True
            await route.fulfill(response=response)
        
        # Match TikTok CDN video URLs
        await page.route("**/*v16-webapp*/**", handle_route)
        await page.route("**/*v19-webapp*/**", handle_route)
        await page.route("**/*v77-webapp*/**", handle_route)
        await page.route("**/*v16-webapp-prime*/**", handle_route)
        
        url = "https://www.tiktok.com/@officialinews/video/7619550440434273553"
        print(f"Fetching {url}")
        await page.goto(url, wait_until="networkidle", timeout=25000)
        
        # Give it a bit more time
        await page.wait_for_timeout(5000)
        
        await browser.close()
    
    if captured["done"]:
        print(f"\nVideo saved: {video_path}")
        print("Loading Whisper model (base)...")
        from faster_whisper import WhisperModel
        model = WhisperModel("base", compute_type="int8")
        segments, info = model.transcribe(video_path, language=None)
        transcript_parts = [seg.text for seg in segments]
        transcript = " ".join(transcript_parts)
        print(f"\n🎤 Language: {info.language} (confidence: {info.language_probability:.2f})")
        print(f"📝 Transcript: {transcript}")
    else:
        print("\n❌ No video captured via route interception.")

if __name__ == "__main__":
    asyncio.run(main())
