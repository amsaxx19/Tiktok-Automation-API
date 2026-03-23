"""
Fully automated transcript extraction for TikTok videos.
Uses Playwright route interception to capture video bytes from TikTok's CDN,
then transcribes the audio.
"""
import asyncio
import os
import tempfile
from typing import Optional

from playwright.async_api import async_playwright

_whisper_model = None

def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        model_size = os.environ.get("WHISPER_MODEL", "base")
        _whisper_model = WhisperModel(model_size, compute_type="int8")
    return _whisper_model


async def transcribe_tiktok_video(video_url: str, timeout_ms: int = 20000) -> Optional[str]:
    video_bytes = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        captured = {"data": None}

        async def handle_video_route(route):
            if captured["data"] is not None:
                await route.continue_()
                return
            try:
                response = await route.fetch()
                body = await response.body()
                if len(body) > 50_000:
                    captured["data"] = body
                await route.fulfill(response=response)
            except Exception:
                await route.continue_()

        # Match TikTok CDN video URL patterns
        for pattern in [
            "**/*v16-webapp*/**",
            "**/*v19-webapp*/**",
            "**/*v77-webapp*/**",
            "**/*v16-webapp-prime*/**",
        ]:
            await page.route(pattern, handle_video_route)

        try:
            await page.goto(video_url, wait_until="networkidle", timeout=timeout_ms)
            await page.wait_for_timeout(4000)
        except Exception:
            pass
        finally:
            video_bytes = captured["data"]
            await browser.close()

    if not video_bytes:
        return None

    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    try:
        tmp.write(video_bytes)
        tmp.flush()
        tmp.close()

        model = _get_whisper_model()
        segments, info = model.transcribe(tmp.name, language=None)
        transcript = " ".join(seg.text.strip() for seg in segments).strip()
        return transcript or None
    except Exception:
        return None
    finally:
        os.unlink(tmp.name)
